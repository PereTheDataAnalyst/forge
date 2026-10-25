"""Connects the MCP library's ports to the platform: gateway, limits and speech.

The ports that read or write the database are in `db_adapters`. The ones here need no database.

Every adapter looks its collaborator up when it is called instead of capturing it, so replacing
`app.state.gateway` or `app.state.rate_limiter` (as the integration tests do) takes effect at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.ports import (
    GenerationRequest,
    GenerationResult,
    RateDecision,
    TranscriptionResult,
    Usage,
)

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.scopes import SCOPE_CHAT_CREATE
from app.integrations.contracts import ChatGateway, GatewayChatInput, GatewayPrincipal
from app.modules.voice.runtime import VoiceRuntime
from app.providers.base import ChatMessage

if TYPE_CHECKING:
    from app.cache.rate_limit import RateLimiter

# A fixed value, so request logs keep a small set of endpoint values for MCP traffic.
MCP_ENDPOINT = "/mcp"
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

log = get_logger("forge.mcp")


def to_forge_error(exc: AppError) -> ForgeError:
    """A platform error as the library's error, so the tool layer can report it."""
    return ForgeError(
        exc.code,
        exc.message,
        http_status=exc.status_code,
        retryable=exc.status_code in _RETRYABLE_STATUSES,
        headers=exc.headers or None,
    )


def _project_id(request: GenerationRequest) -> uuid.UUID:
    try:
        return uuid.UUID(request.project_id or "")
    except ValueError:
        raise ForgeError(
            ErrorCode.INTERNAL, "The MCP call has no valid project.", http_status=500
        ) from None


class McpModelGateway:
    """The library's `ModelGateway` port, implemented with the platform gateway.

    A tool call is a normal gateway request: rate limit, request log and usage metering are the
    same as for any caller, and the request is logged with source `mcp`. It is tied to the
    project, so the gateway's limit is metered per project. A throttled call is reported to the
    agent straight away instead of waiting, so a tool call never hangs.
    """

    def __init__(self, get_gateway: Callable[[], ChatGateway]) -> None:
        self._get_gateway = get_gateway

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        principal = GatewayPrincipal(
            project_id=_project_id(request),
            scopes=frozenset({SCOPE_CHAT_CREATE}),
            source="mcp",
        )
        payload = GatewayChatInput(
            messages=tuple(
                ChatMessage(role=message.role, content=message.content)
                for message in request.messages
            ),
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            language=request.language,
        )
        try:
            output = await self._get_gateway().chat(
                principal,
                payload,
                request_id=request.request_id or uuid.uuid4().hex,
                endpoint=MCP_ENDPOINT,
            )
        except AppError as exc:
            raise to_forge_error(exc) from exc
        return GenerationResult(
            text=output.text,
            model=output.model,
            usage=Usage(
                prompt_tokens=output.usage.prompt_tokens,
                completion_tokens=output.usage.completion_tokens,
                total_tokens=output.usage.total_tokens,
            ),
            latency_ms=float(output.latency_ms),
            finish_reason=output.finish_reason,
        )


class PlatformRateLimiter:
    """The library's `RateLimiter` port on the platform's Redis limiter, shared by all workers.

    If Redis is unavailable the platform limiter raises, and the library then refuses the call.
    """

    def __init__(self, get_limiter: Callable[[], RateLimiter]) -> None:
        self._get_limiter = get_limiter

    async def check(self, key: str, limit: int, window_seconds: int) -> RateDecision:
        result = await self._get_limiter().hit(f"rl:mcp:{key}"[:190], limit, window_seconds)
        return RateDecision(
            allowed=result.allowed,
            remaining=result.remaining,
            retry_after_seconds=result.reset_seconds,
        )


class SharedAsrProvider:
    """The library's `AsrProvider` port, using the speech worker client that Voice already built.

    One client and one limit on parallel uploads serve both Voice and MCP, so the worker is never
    asked to handle more than the platform allows.
    """

    def __init__(self, runtime: VoiceRuntime) -> None:
        self._runtime = runtime

    async def transcribe(
        self, *, audio: bytes, audio_format: str, language: str, request_id: str | None = None
    ) -> TranscriptionResult:
        provider = await self._runtime.get_provider()
        if provider is None:
            if self._runtime.error is None:
                raise ForgeError(
                    ErrorCode.PROVIDER_NOT_CONFIGURED,
                    "No speech recognition worker is connected to this deployment.",
                    http_status=503,
                )
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The speech recognition worker cannot be reached from this deployment.",
                http_status=503,
                retryable=True,
            )
        if not self._runtime.try_acquire():
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The speech service is handling other uploads. Try again shortly.",
                http_status=503,
                retryable=True,
                headers={"Retry-After": "2"},
            )
        try:
            return await provider.transcribe(
                audio=audio, audio_format=audio_format, language=language, request_id=request_id
            )
        finally:
            self._runtime.release()


class McpLogObserver:
    """Writes the library's events to the application log. Keys are prefixed to avoid clashes."""

    def __init__(self) -> None:
        self._log = get_logger("forge.mcp.events")

    def emit(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("event", "mcp.event"))[:60]
        extra = {f"mcp_{key}": value for key, value in event.items() if key != "event"}
        self._log.info(name, extra=extra)
