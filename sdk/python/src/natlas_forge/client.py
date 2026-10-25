"""The clients: ``Forge`` (blocking) and ``AsyncForge`` (awaitable).

Both expose ``models.list()``, ``chat.create()`` and ``audio.transcribe()``. Every result and every
error carries the request ID, which is also the ID shown in the Request logs page.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import re
import time
from typing import (
    Any,
    BinaryIO,
    Callable,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import quote, urlsplit

from . import _transport
from ._version import __version__
from .errors import APIConnectionError, APITimeoutError, ForgeError, error_from_response
from .results import (
    ChatCompletion,
    ChatMessage,
    Model,
    ModelList,
    RateLimit,
    Transcription,
    TranscriptionAudio,
    Usage,
)

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY = 30.0
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_BAD_KEY_CHARS = re.compile(r"[\x00-\x20\x7f]")
# A 503 with one of these codes will not change by waiting, so it is not retried.
_NO_RETRY_CODES = {"provider_not_configured", "provider_misconfigured", "mcp_disabled"}
_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
}

Message = Union[ChatMessage, Mapping[str, str]]
AudioInput = Union[bytes, bytearray, memoryview, str, "os.PathLike[str]", BinaryIO]


def _normalise_base_url(value: str, allow_insecure_http: bool) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base_url must start with https:// (or http:// for a local server).")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url must not contain credentials, a query or a fragment.")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK and not allow_insecure_http:
        raise ValueError(
            "Refusing to send an API key over plain http to a remote host. Use https://, "
            "or pass allow_insecure_http=True for a private network you control."
        )
    path = parts.path.rstrip("/")
    if not path.endswith("/api/v1"):
        path += "/api/v1"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _integer(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _rate_limit(headers: Mapping[str, str]) -> RateLimit:
    return RateLimit(
        limit=_integer(headers.get("x-ratelimit-limit")),
        remaining=_integer(headers.get("x-ratelimit-remaining")),
        reset_seconds=_integer(headers.get("x-ratelimit-reset")),
    )


def _bad_response(status: int, headers: Mapping[str, str], what: str) -> ForgeError:
    return ForgeError(
        f"The server sent a response this client could not read ({what}).",
        status=status,
        code="invalid_response",
        request_id=headers.get("x-request-id"),
    )


class _Core:
    """Sends requests, retries the ones that are safe to repeat, and turns errors into types."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        transport: _transport.Transport,
        sleep: Callable[[float], None],
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport
        self._sleep = sleep
        self._user_agent = f"natlas-forge-python/{__version__} python/{platform.python_version()}"

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        raw_body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Mapping[str, str]]:
        url = self.base_url + path
        if query:
            url += "?" + "&".join(f"{quote(k)}={quote(str(v), safe='')}" for k, v in query.items())
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "Connection": "close",
        }
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif raw_body is not None:
            body = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        if request_id is not None:
            if not _REQUEST_ID.match(request_id):
                raise ValueError("request_id must be 8 to 64 letters, digits, dots, dashes or underscores.")
            headers["X-Request-ID"] = request_id
        idempotent = method in ("GET", "HEAD")
        wait = self.timeout if timeout is None else timeout
        attempt = 0
        while True:
            try:
                raw = self._transport(method, url, headers, body, wait)
            except _transport.TransportError as exc:
                # A read is always safe to repeat. A write is safe only if the failure came before
                # anything was sent. After that the server may have done the work.
                safe = idempotent or exc.connect_stage
                if safe and attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                if exc.timed_out:
                    raise APITimeoutError(
                        f"The request to the Forge API timed out after {wait:g} seconds."
                    ) from None
                raise APIConnectionError(
                    f"Could not reach the Forge API ({exc.reason}). Check base_url and your network."
                ) from None
            except _transport.ResponseTooLarge:
                raise ForgeError("The response was larger than this client accepts.", code="invalid_response") from None
            parsed = self._parse(raw)
            if 200 <= raw.status < 300:
                if not isinstance(parsed, dict):
                    raise _bad_response(raw.status, raw.headers, "not a JSON object")
                return parsed, raw.headers
            if 300 <= raw.status < 400:
                raise ForgeError(
                    "The server redirected the request. Redirects are not followed, so your API "
                    "key is never sent to another address. Check base_url.",
                    status=raw.status,
                    code="unexpected_redirect",
                    request_id=raw.headers.get("x-request-id"),
                )
            error = error_from_response(raw.status, parsed if isinstance(parsed, dict) else None, raw.headers)
            if attempt < self.max_retries and self._retryable(method, error):
                delay = self._backoff(attempt, error.retry_after)
                if delay is not None:
                    self._sleep(delay)
                    attempt += 1
                    continue
            raise error

    @staticmethod
    def _parse(raw: _transport.RawResponse) -> Any:
        if not raw.body:
            return None
        try:
            return json.loads(raw.body.decode("utf-8"))
        except ValueError:
            return None

    @staticmethod
    def _retryable(method: str, error: ForgeError) -> bool:
        if error.code in _NO_RETRY_CODES:
            return False
        if error.status in (429, 503):
            return True  # refused before any work was done, safe for every method
        return method in ("GET", "HEAD") and error.status in (502, 504)

    @staticmethod
    def _backoff(attempt: int, retry_after: Optional[float]) -> Optional[float]:
        if retry_after is not None:
            return retry_after if retry_after <= MAX_RETRY_DELAY else None
        return min(0.5 * (2**attempt), 8.0) * random.uniform(0.5, 1.0)


def _message_dict(message: Message) -> Dict[str, str]:
    if isinstance(message, ChatMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, Mapping) and isinstance(message.get("role"), str) and isinstance(message.get("content"), str):
        return {"role": message["role"], "content": message["content"]}
    raise TypeError("Each message must be a ChatMessage or a dict with string 'role' and 'content'.")


class _Models:
    def __init__(self, core: _Core) -> None:
        self._core = core

    def list(self, *, request_id: Optional[str] = None, timeout: Optional[float] = None) -> ModelList:
        """The models available to this key. Needs the ``models.read`` scope."""
        body, headers = self._core.request("GET", "/models", request_id=request_id, timeout=timeout)
        try:
            models = tuple(
                Model(
                    id=item["id"],
                    kind=item["kind"],
                    display_name=item["display_name"],
                    architecture=item.get("architecture"),
                    parameters=item.get("parameters"),
                    languages=tuple(item.get("languages") or ()),
                    context_length_tokens=item.get("context_length_tokens"),
                    license=item["license"],
                    source_url=item["source_url"],
                    attribution=item.get("attribution"),
                    status=item["status"],
                )
                for item in body["data"]
            )
        except (KeyError, TypeError):
            raise _bad_response(200, headers, "models") from None
        return ModelList(
            request_id=headers.get("x-request-id", ""), data=models, rate_limit=_rate_limit(headers)
        )


class _Chat:
    def __init__(self, core: _Core) -> None:
        self._core = core

    def create(
        self,
        messages: Sequence[Message],
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        repetition_penalty: Optional[float] = None,
        language: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> ChatCompletion:
        """Generate a reply. Needs the ``chat.create`` scope. The server checks every value."""
        if isinstance(messages, (str, bytes)) or not messages:
            raise ValueError("messages must be a non-empty list.")
        payload: Dict[str, Any] = {"messages": [_message_dict(item) for item in messages]}
        for name, value in (
            ("model", model),
            ("max_tokens", max_tokens),
            ("temperature", temperature),
            ("top_p", top_p),
            ("repetition_penalty", repetition_penalty),
            ("language", language),
        ):
            if value is not None:
                payload[name] = value
        body, headers = self._core.request(
            "POST", "/chat", json_body=payload, request_id=request_id, timeout=timeout
        )
        try:
            usage = body["usage"]
            return ChatCompletion(
                request_id=str(body.get("request_id") or headers.get("x-request-id", "")),
                model=body["model"],
                message=ChatMessage(role=body["message"]["role"], content=body["message"]["content"]),
                finish_reason=body["finish_reason"],
                usage=Usage(
                    prompt_tokens=int(usage["prompt_tokens"]),
                    completion_tokens=int(usage["completion_tokens"]),
                    total_tokens=int(usage["total_tokens"]),
                ),
                latency_ms=int(body["latency_ms"]),
                rate_limit=_rate_limit(headers),
            )
        except (KeyError, TypeError, ValueError):
            raise _bad_response(200, headers, "chat") from None


def _read_audio(audio: AudioInput, content_type: Optional[str]) -> Tuple[bytes, str]:
    if isinstance(audio, (bytes, bytearray, memoryview)):
        data, guessed = bytes(audio), None
    elif isinstance(audio, (str, os.PathLike)):
        path = os.fspath(audio)
        guessed = _AUDIO_TYPES.get(os.path.splitext(path)[1].lower())
        size = os.path.getsize(path)
        if size > MAX_AUDIO_BYTES:
            raise ValueError(f"The audio file is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MiB.")
        with open(path, "rb") as handle:
            data = handle.read()
    elif hasattr(audio, "read"):
        data = audio.read(MAX_AUDIO_BYTES + 1)
        guessed = _AUDIO_TYPES.get(os.path.splitext(getattr(audio, "name", "") or "")[1].lower())
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("A file object passed as audio must be opened in binary mode.")
    else:
        raise TypeError("audio must be bytes, a file path, or a binary file object.")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"The audio is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MiB.")
    return bytes(data), content_type or guessed or "application/octet-stream"


class _Audio:
    def __init__(self, core: _Core) -> None:
        self._core = core

    def transcribe(
        self,
        audio: AudioInput,
        *,
        language: str,
        content_type: Optional[str] = None,
        request_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Transcription:
        """Transcribe speech in ``en``, ``ha``, ``ig`` or ``yo``. Needs the ``asr.transcribe`` scope.

        ``audio`` is bytes, a file path or a binary file object, at most 10 MiB and 30 seconds as
        the server checks. If the deployment has no speech worker connected the call fails with
        code ``provider_not_configured``.
        """
        data, kind = _read_audio(audio, content_type)
        body, headers = self._core.request(
            "POST",
            "/audio/transcriptions",
            query={"language": language},
            raw_body=data,
            content_type=kind,
            request_id=request_id,
            timeout=timeout,
        )
        try:
            info = body["audio"]
            return Transcription(
                request_id=str(body.get("request_id") or headers.get("x-request-id", "")),
                language=body["language"],
                model=body["model"],
                text=body["text"],
                audio=TranscriptionAudio(
                    format=info["format"],
                    size_bytes=int(info["size_bytes"]),
                    duration_seconds=info.get("duration_seconds"),
                ),
                latency_ms=int(body["latency_ms"]),
                attribution=body.get("attribution", ""),
                rate_limit=_rate_limit(headers),
            )
        except (KeyError, TypeError, ValueError):
            raise _bad_response(200, headers, "transcription") from None


def _build_core(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float,
    max_retries: int,
    allow_insecure_http: bool,
    transport: Optional[_transport.Transport],
    sleep: Callable[[float], None],
) -> _Core:
    key = api_key if api_key is not None else os.environ.get("FORGE_API_KEY")
    address = base_url if base_url is not None else os.environ.get("FORGE_BASE_URL")
    if not key or _BAD_KEY_CHARS.search(key):
        raise ValueError("Pass api_key, or set FORGE_API_KEY. A key has no spaces or line breaks.")
    if not address:
        raise ValueError("Pass base_url, or set FORGE_BASE_URL, for example https://forge.example.com")
    if not (isinstance(timeout, (int, float)) and 0 < timeout <= 600):
        raise ValueError("timeout must be between 0 and 600 seconds.")
    if not (isinstance(max_retries, int) and 0 <= max_retries <= 10):
        raise ValueError("max_retries must be between 0 and 10.")
    return _Core(
        key,
        _normalise_base_url(address, allow_insecure_http),
        float(timeout),
        max_retries,
        transport or _transport.default_transport,
        sleep,
    )


def _masked(key: str) -> str:
    return key[:8] + "..." if len(key) > 8 else "..."


class Forge:
    """A blocking client. Create one and reuse it; it holds no connection between calls."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        allow_insecure_http: bool = False,
        transport: Optional[_transport.Transport] = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._core = _build_core(api_key, base_url, timeout, max_retries, allow_insecure_http, transport, _sleep)
        self.models = _Models(self._core)
        self.chat = _Chat(self._core)
        self.audio = _Audio(self._core)

    @property
    def base_url(self) -> str:
        return self._core.base_url

    def __repr__(self) -> str:
        return f"Forge(base_url={self._core.base_url!r}, api_key={_masked(self._core.api_key)!r})"


class _AsyncProxy:
    """Runs a blocking resource method in a worker thread so it can be awaited."""

    def __init__(self, resource: Any) -> None:
        self._resource = resource

    def __getattr__(self, name: str) -> Callable[..., Any]:
        method = getattr(self._resource, name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(method, *args, **kwargs)

        return call


class AsyncForge:
    """An awaitable client. Calls run on worker threads, so they never block the event loop."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        allow_insecure_http: bool = False,
        transport: Optional[_transport.Transport] = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sync = Forge(
            api_key,
            base_url,
            timeout=timeout,
            max_retries=max_retries,
            allow_insecure_http=allow_insecure_http,
            transport=transport,
            _sleep=_sleep,
        )
        self.models = _AsyncProxy(self._sync.models)
        self.chat = _AsyncProxy(self._sync.chat)
        self.audio = _AsyncProxy(self._sync.audio)

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    def __repr__(self) -> str:
        return repr(self._sync).replace("Forge(", "AsyncForge(", 1)


__all__ = ["AsyncForge", "Forge"]
