"""HTTP client for the Forge N-ATLAS ASR service.

The official N-ATLAS ASR models are Hugging Face Whisper-Small fine-tunes. They are
served by the Forge ASR worker (``forge_integrations.asr_service``). This client speaks
that service's contract:

    POST {base_url}/v1/audio/transcriptions   multipart/form-data
         file=<audio>  language=<ha|ig|yo|en>
    200 -> {"text", "language", "model", "duration_seconds", "request_id"}
    4xx/5xx -> {"error": {"code", "message", "request_id"}}

This is a Forge service contract, not an official N-ATLAS API. The target host is
operator-configured and allowlisted. It is re-resolved and re-validated before every call
and the connection is pinned to the validated addresses, so a DNS answer that changes
between validation and connection cannot redirect the request. Redirects are never
followed. Credentials are sent only in the Authorization header.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import re
import ssl
import time
import uuid
from typing import Any

from ..errors import ConfigError, ErrorCode, ForgeError
from ..ports import TranscriptionResult
from ..security.http_limits import read_all_limited
from ..security.outbound import OutboundTarget, Resolver, validate_outbound_url
from ..security.pinned_http import open_pinned_connection
from ..security.redaction import redact_text
from .audio import CANONICAL_MIME, SUFFIX, AudioFormat
from .languages import resolve_asr_language

MAX_TEXT_CHARS = 20_000
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")
_PASSTHROUGH_CODES = {
    ErrorCode.AUDIO_EMPTY,
    ErrorCode.AUDIO_TOO_LARGE,
    ErrorCode.AUDIO_TOO_LONG,
    ErrorCode.AUDIO_UNSUPPORTED_TYPE,
    ErrorCode.AUDIO_SIGNATURE_MISMATCH,
    ErrorCode.AUDIO_CORRUPT,
    ErrorCode.LANGUAGE_UNSUPPORTED,
}


def build_multipart(fields: dict[str, str], file_field: str, filename: str, content_type: str, payload: bytes) -> tuple[bytes, str]:
    boundary = "forge" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    chunks.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(payload)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class HttpAsrProvider:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        allowed_hosts: list[str] | tuple[str, ...],
        allow_private: bool = False,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 262_144,
        path: str = "/v1/audio/transcriptions",
        resolver: Resolver | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if token is not None and not _TOKEN_RE.fullmatch(token):
            raise ConfigError("The ASR service token contains characters that are not allowed in an HTTP header.")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._allowed_hosts = tuple(allowed_hosts)
        self._allow_private = allow_private
        self._allow_insecure_http = allow_insecure_http
        self._timeout = timeout_seconds
        self._max_response = max_response_bytes
        self._path = path
        self._resolver = resolver
        self._ssl_context = ssl_context  # for a private CA; certificates are always verified
        self._target = self._validate()  # fail at construction, not on the first request

    def _validate(self) -> OutboundTarget:
        return validate_outbound_url(
            self._base_url,
            allowed_hosts=self._allowed_hosts,
            allow_private=self._allow_private,
            allow_insecure_http=self._allow_insecure_http,
            resolver=self._resolver,
        )

    async def transcribe(
        self, *, audio: bytes, audio_format: str, language: str, request_id: str | None = None
    ) -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, audio, audio_format, language, request_id)

    def _transcribe_sync(self, audio: bytes, audio_format: str, language: str, request_id: str | None) -> TranscriptionResult:
        facts = resolve_asr_language(language)
        try:
            fmt = AudioFormat(audio_format)
        except ValueError:
            raise ForgeError(ErrorCode.AUDIO_UNSUPPORTED_TYPE, "Unsupported audio format.", http_status=415) from None
        target = self._validate()  # re-check DNS and policy before every connection
        rid = request_id if request_id and _REQUEST_ID_RE.fullmatch(request_id) else uuid.uuid4().hex
        body, content_type = build_multipart(
            {"language": facts.language}, "file", f"audio{SUFFIX[fmt]}", CANONICAL_MIME[fmt], audio
        )
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Accept": "application/json",
            "X-Request-ID": rid,
            "User-Agent": "forge-integrations/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        connection = open_pinned_connection(target, timeout=self._timeout, context=self._ssl_context)

        started = time.perf_counter()
        try:
            connection.request("POST", self._request_path(), body=body, headers=headers)
            response = connection.getresponse()
            try:
                raw = read_all_limited(response.read, self._max_response)
            except ForgeError:
                raise ForgeError(
                    ErrorCode.PROVIDER_ERROR, "The ASR service returned an oversized response.", http_status=502
                ) from None
            status = response.status
        except ForgeError:
            raise
        except TimeoutError:
            raise ForgeError(ErrorCode.PROVIDER_TIMEOUT, "The ASR service did not respond in time.", http_status=504, retryable=True) from None
        except (OSError, http.client.HTTPException):
            raise ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "The ASR service is unreachable.", http_status=503, retryable=True) from None
        finally:
            connection.close()
        latency_ms = (time.perf_counter() - started) * 1000

        if status == 200:
            return self._parse_success(raw, facts.language, latency_ms, rid)
        raise self._map_error(status, raw)

    def _request_path(self) -> str:
        base_path = self._target.path.rstrip("/")
        return (base_path if base_path not in ("", "/") else "") + self._path

    def _parse_success(self, raw: bytes, language: str, latency_ms: float, request_id: str) -> TranscriptionResult:
        try:
            data: Any = json.loads(raw.decode("utf-8"))
            text = data["text"]
            model = data["model"]
            duration = data.get("duration_seconds")
            if not isinstance(text, str) or not isinstance(model, str):
                raise TypeError
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))):
                raise TypeError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise ForgeError(ErrorCode.PROVIDER_ERROR, "The ASR service returned an unexpected response.", http_status=502) from None
        return TranscriptionResult(
            text=text[:MAX_TEXT_CHARS],
            language=language,
            model=model,
            duration_seconds=None if duration is None else float(duration),
            latency_ms=round(latency_ms, 2),
            request_id=request_id,
        )

    def _map_error(self, status: int, raw: bytes) -> ForgeError:
        code, message = None, None
        try:
            err = json.loads(raw.decode("utf-8")).get("error", {})
            code, message = err.get("code"), err.get("message")
        except (ValueError, AttributeError, UnicodeDecodeError):
            pass
        if code in _PASSTHROUGH_CODES and status in (413, 415, 422):
            return ForgeError(code, redact_text(str(message or "The audio was rejected.")), http_status=status)
        if status in (401, 403):  # our credentials to the service failed; not the caller's fault
            return ForgeError(ErrorCode.PROVIDER_ERROR, "The ASR service rejected Forge's credentials.", http_status=502)
        if status in (429, 503):
            return ForgeError(ErrorCode.PROVIDER_UNAVAILABLE, "The ASR service is busy or unavailable.", http_status=503, retryable=True)
        if status == 504:
            return ForgeError(ErrorCode.PROVIDER_TIMEOUT, "The ASR service timed out.", http_status=504, retryable=True)
        return ForgeError(ErrorCode.PROVIDER_ERROR, "The ASR service failed to process the request.", http_status=502, retryable=status >= 500)
