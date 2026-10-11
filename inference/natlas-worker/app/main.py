"""N-ATLAS inference worker API.

Run with:  uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8081

The worker must stay on a private address. Every endpoint except /healthz requires the shared
bearer token. Error responses use {"error": {"code": "...", "message": "..."}}.
"""

import asyncio
import hmac
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import WorkerSettings, get_settings
from app.engine import ContextLengthExceeded, EngineState, GenerationOutput, NAtlasEngine
from app.schemas import ChatRequest, ChatResponse

log = logging.getLogger("natlas.worker")


class Engine(Protocol):
    @property
    def state(self) -> EngineState: ...

    def info(self) -> dict[str, Any]: ...

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_new_tokens: int,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
    ) -> GenerationOutput: ...


class WorkerError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


class Admission:
    """Bounds concurrent generations and the number of requests allowed to wait."""

    def __init__(self, max_concurrency: int, max_queue: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._waiting = 0
        self._max_queue = max_queue

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._semaphore.locked() and self._waiting >= self._max_queue:
            raise WorkerError(
                429, "busy", "The worker queue is full. Retry shortly.", {"Retry-After": "5"}
            )
        self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting -= 1
        try:
            yield
        finally:
            self._semaphore.release()


def _error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def create_app(settings: WorkerSettings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    real_engine = engine is None
    active_engine: Engine = engine if engine is not None else NAtlasEngine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if real_engine and settings.load_on_startup:
            active_engine.start_loading()  # type: ignore[attr-defined]
        yield

    app = FastAPI(
        title="N-ATLAS inference worker",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = active_engine
    app.state.admission = Admission(settings.max_concurrency, settings.max_queue)

    @app.exception_handler(WorkerError)
    async def worker_error_handler(request: Request, exc: WorkerError):
        return _error_response(exc.status_code, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fields = ", ".join(
            ".".join(str(part) for part in err.get("loc", ()) if part != "body") or "body"
            for err in exc.errors()[:5]
        )
        return _error_response(422, "invalid_request", f"The request is not valid: {fields}.")

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error_response(exc.status_code, code, "The request failed.")

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        expected = settings.token.get_secret_value()
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode("utf-8"), expected.encode("utf-8")
        ):
            raise WorkerError(401, "unauthorized", "Missing or invalid credentials.")

    def require_small_body(request: Request) -> None:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > settings.max_body_bytes:
            raise WorkerError(413, "payload_too_large", "The request body is too large.")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness only. Reveals nothing about the model."""
        return {"status": "ok"}

    @app.get("/v1/info", dependencies=[Depends(require_token)])
    async def info() -> dict[str, Any]:
        return active_engine.info()

    @app.post(
        "/v1/chat",
        response_model=ChatResponse,
        dependencies=[Depends(require_token), Depends(require_small_body)],
    )
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        request_id = request.headers.get("x-request-id", "-")[:64]
        if active_engine.state is EngineState.LOADING:
            raise WorkerError(
                503, "model_loading", "The model is still loading.", {"Retry-After": "15"}
            )
        if active_engine.state is EngineState.FAILED:
            raise WorkerError(503, "model_failed", "The model failed to load. Check the worker log.")
        if payload.max_new_tokens > settings.max_new_tokens_limit:
            raise WorkerError(
                422, "invalid_request", f"max_new_tokens must not exceed {settings.max_new_tokens_limit}."
            )
        if len(payload.messages) > settings.max_messages:
            raise WorkerError(422, "invalid_request", "Too many messages.")
        if sum(len(m.content) for m in payload.messages) > settings.max_prompt_chars:
            raise WorkerError(422, "invalid_request", "The combined message length is too large.")

        messages = [{"role": m.role, "content": m.content} for m in payload.messages]
        admission: Admission = request.app.state.admission
        async with admission.slot():
            try:
                output = await asyncio.wait_for(
                    asyncio.to_thread(
                        active_engine.generate,
                        messages,
                        max_new_tokens=payload.max_new_tokens,
                        temperature=payload.temperature,
                        top_p=payload.top_p,
                        repetition_penalty=payload.repetition_penalty,
                    ),
                    timeout=settings.max_generation_seconds + 30,
                )
            except ContextLengthExceeded as exc:
                raise WorkerError(
                    422,
                    "context_length_exceeded",
                    f"The prompt has {exc.prompt_tokens} tokens and {exc.requested_new_tokens} new "
                    f"tokens were requested, which exceeds the limit of {exc.limit} tokens.",
                ) from exc
            except TimeoutError as exc:
                raise WorkerError(504, "generation_timeout", "Generation took too long.") from exc
            except Exception as exc:
                if type(exc).__name__ == "OutOfMemoryError":
                    log.error("generation.out_of_memory", extra={"request_id": request_id})
                    raise WorkerError(
                        503, "busy", "The worker is out of memory. Retry shortly.", {"Retry-After": "10"}
                    ) from exc
                log.exception("generation.failed", extra={"request_id": request_id})
                raise WorkerError(500, "generation_failed", "Generation failed.") from exc

        log.info(
            "generation.completed",
            extra={
                "request_id": request_id,
                "prompt_tokens": output.prompt_tokens,
                "completion_tokens": output.completion_tokens,
                "generation_ms": output.generation_ms,
                "finish_reason": output.finish_reason,
            },
        )
        return ChatResponse(
            text=output.text,
            finish_reason=output.finish_reason,  # type: ignore[arg-type]
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            model_id=settings.model_id,
            generation_ms=output.generation_ms,
        )

    return app


def generate_token() -> str:
    """Helper for operators: python -c "from app.main import generate_token; print(generate_token())"."""
    return secrets.token_urlsafe(48)
