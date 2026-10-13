"""ASGI application for the Forge ASR worker.

Endpoints
  GET  /healthz                   liveness, no authentication, no details
  GET  /readyz                    readiness, no authentication, no details
  GET  /v1/models                 verified model facts and worker status (authenticated)
  POST /v1/audio/transcriptions   multipart/form-data: file, language (authenticated)

Request flow for a transcription: authenticate before reading the body, bound the body,
parse at most one file and a few small fields, validate the audio by signature, decode it
in a hardened ffmpeg subprocess to 16 kHz mono PCM, run the official model, return text.
Audio and transcripts are never logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..errors import ErrorCode, ForgeError
from ..natlas_facts import VERIFIED_ON, model_info_payload
from ..observability import NullObserver
from ..ports import Observer
from ..security.safe_files import SecureTempStore
from ..voice.audio import suffix_for, validate_audio_upload
from ..voice.languages import resolve_asr_language
from ..voice.transcode import decode_to_pcm16k, ffmpeg_available
from ..web.asgi import ASGIApp, internal_error, json_body, request_id_for
from ..web.limits import BodySizeLimitMiddleware
from .engine import AsrEngine
from .gate import WorkGate
from .settings import AsrServiceSettings

#: Multipart framing overhead allowed on top of the audio limit.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

_BASE_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


def create_app(settings: AsrServiceSettings, engine: AsrEngine, *, observer: Observer | None = None) -> ASGIApp:
    obs: Observer = observer or NullObserver()
    gate = WorkGate(settings.max_concurrency, settings.max_waiting)
    store = SecureTempStore(settings.temp_dir)
    expected_digest = hashlib.sha256(settings.token.encode("utf-8")).digest() if settings.token else None

    def emit(event: dict[str, Any]) -> None:
        try:
            obs.emit(event)
        except Exception:  # noqa: BLE001 - observability must never break a request
            pass

    def respond(request: Request, status: int, payload: Any, headers: dict[str, str] | None = None) -> Response:
        merged = {**_BASE_HEADERS, "x-request-id": request_id_for(request.scope)}
        if headers:
            merged.update({k.lower(): v for k, v in headers.items()})
        return Response(json_body(payload), status_code=status, media_type="application/json", headers=merged)

    def error_response(request: Request, exc: ForgeError) -> Response:
        return respond(request, exc.http_status, exc.to_payload(request_id_for(request.scope)), exc.headers)

    def authenticate(request: Request) -> None:
        if expected_digest is None:
            return
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        value = value.strip()
        presented = hashlib.sha256(value.encode("utf-8")).digest() if scheme.lower() == "bearer" and value else b""
        if not presented or not hmac.compare_digest(presented, expected_digest):
            raise ForgeError(
                ErrorCode.UNAUTHORIZED,
                "A valid bearer token is required.",
                http_status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def healthz(request: Request) -> Response:
        return respond(request, 200, {"status": "ok"})

    async def readyz(request: Request) -> Response:
        status = engine.status()
        loaded = set(status.get("loaded_languages", ()))
        ready = (
            bool(status.get("runtime_available", True))
            and set(settings.preload_languages) <= loaded
            and ffmpeg_available(settings.ffmpeg_path)
        )
        return respond(request, 200 if ready else 503, {"status": "ready" if ready else "not_ready"})

    async def models(request: Request) -> Response:
        try:
            authenticate(request)
        except ForgeError as exc:
            return error_response(request, exc)
        info = model_info_payload()
        return respond(
            request,
            200,
            {
                "verified_on": VERIFIED_ON,
                "models": info["asr"],
                "engine": engine.status(),
                "ffmpeg_available": ffmpeg_available(settings.ffmpeg_path),
                "limits": {
                    "max_seconds": settings.max_seconds,
                    "max_upload_bytes": settings.max_upload_bytes,
                    "max_concurrency": settings.max_concurrency,
                },
                "licence": info["licence"],
            },
        )

    async def transcriptions(request: Request) -> Response:
        started = time.perf_counter()
        rid = request_id_for(request.scope)
        event: dict[str, Any] = {"event": "asr.request", "request_id": rid}
        try:
            authenticate(request)
            content_type = request.headers.get("content-type", "").lower()
            if not content_type.startswith("multipart/form-data"):
                raise ForgeError(
                    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    "Send the audio as multipart/form-data with a 'file' part and a 'language' field.",
                    http_status=415,
                )
            try:
                form = await request.form(max_files=1, max_fields=4, max_part_size=4096)
            except (MultiPartException, StarletteHTTPException, ValueError):
                # ValueError covers python-multipart's parse errors for malformed bodies.
                raise ForgeError(
                    ErrorCode.INVALID_REQUEST,
                    "The multipart body is malformed or contains too many parts.",
                    http_status=400,
                ) from None
            try:
                upload = form.get("file")
                language_field = form.get("language")
                if not isinstance(upload, UploadFile):
                    raise ForgeError(ErrorCode.INVALID_ARGUMENT, "A multipart 'file' part is required.", http_status=422)
                facts = resolve_asr_language(language_field if isinstance(language_field, str) else None)
                event["language"] = facts.language
                data = await upload.read(settings.max_upload_bytes + 1)
                declared_type = upload.content_type
            finally:
                await form.close()

            validated = validate_audio_upload(
                data, declared_type, max_bytes=settings.max_upload_bytes, max_seconds=settings.max_seconds
            )
            event.update(format=validated.format.value, size_bytes=validated.size_bytes)

            def job() -> tuple[Any, Any]:
                with store.write(data, suffix_for(validated.format)) as path:
                    decoded = decode_to_pcm16k(
                        path,
                        validated.format,
                        max_seconds=settings.max_seconds,
                        timeout_seconds=settings.decode_timeout_seconds,
                        ffmpeg_path=settings.ffmpeg_path,
                    )
                return decoded, engine.transcribe(decoded.pcm_s16le, facts.language)

            decoded, outcome = await gate.run(
                job, timeout=settings.decode_timeout_seconds + settings.inference_timeout_seconds
            )
            event.update(status=200, audio_seconds=decoded.duration_seconds, model=outcome.model_repo_id)
            return respond(
                request,
                200,
                {
                    "text": outcome.text,
                    "language": facts.language,
                    "model": outcome.model_repo_id,
                    "duration_seconds": decoded.duration_seconds,
                    "request_id": rid,
                },
            )
        except ForgeError as exc:
            event.update(status=exc.http_status, error_code=exc.code)
            return error_response(request, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak internals; type name only is logged
            event.update(status=500, error_code=ErrorCode.INTERNAL, exception_type=type(exc).__name__)
            return error_response(request, internal_error())
        finally:
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            emit(event)

    async def http_exception_handler(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        if exc.status_code == 404:
            error = ForgeError(ErrorCode.NOT_FOUND, "Not found.", http_status=404)
        elif exc.status_code == 405:
            error = ForgeError(
                ErrorCode.METHOD_NOT_ALLOWED,
                "Method not allowed.",
                http_status=405,
                headers=dict(exc.headers or {}),
            )
        else:
            error = ForgeError(ErrorCode.INVALID_REQUEST, "The request could not be processed.", http_status=exc.status_code)
        return error_response(request, error)

    async def forge_error_handler(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, ForgeError)
        return error_response(request, exc)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await asyncio.to_thread(store.cleanup_stale)
        if settings.preload_languages:
            await asyncio.to_thread(engine.preload, settings.preload_languages)
        yield

    application = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/audio/transcriptions", transcriptions, methods=["POST"]),
        ],
        exception_handlers={
            StarletteHTTPException: http_exception_handler,
            ForgeError: forge_error_handler,
        },
        lifespan=lifespan,
    )
    return BodySizeLimitMiddleware(application, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES)
