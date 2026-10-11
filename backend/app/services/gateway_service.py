"""The N-ATLAS gateway: authorisation, rate limiting, validation, provider call and telemetry.

Every entry point (REST, playground, MCP, evaluation) goes through `GatewayService.chat` so
that scope checks, limits, request logs and usage metering are identical everywhere.
"""

import time

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.rate_limit import RateLimiter
from app.core.config import Settings
from app.core.errors import AppError, Forbidden, NotFound, RateLimited, UnprocessableEntity
from app.core.logging import get_logger
from app.core.request_context import annotate
from app.core.scopes import SCOPE_CHAT_CREATE, SCOPE_MODELS_READ
from app.db.base import utcnow
from app.db.models.telemetry import RequestLog, UsageDaily
from app.integrations.contracts import GatewayChatInput, GatewayChatOutput, GatewayPrincipal
from app.providers.base import (
    ChatRequest,
    ChatResult,
    GenerationParams,
    ModelInfo,
    ModelProvider,
    ProviderError,
    Usage,
)

log = get_logger(__name__)


class GatewayService:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: ModelProvider,
        limiter: RateLimiter,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._limiter = limiter
        self._session_factory = session_factory

    # Helpers ----------------------------------------------------------------------------

    @staticmethod
    def _require_scope(principal: GatewayPrincipal, scope: str) -> None:
        if not principal.has_scope(scope):
            raise Forbidden(
                f"This credential does not have the '{scope}' scope.", code="insufficient_scope"
            )

    @staticmethod
    def _bucket(principal: GatewayPrincipal) -> str:
        if principal.api_key_id is not None:
            return f"rl:gateway:key:{principal.api_key_id}"
        if principal.user_id is not None:
            return f"rl:gateway:user:{principal.user_id}"
        return f"rl:gateway:project:{principal.project_id}"

    async def _enforce_rate_limit(self, principal: GatewayPrincipal) -> dict[str, str]:
        result = await self._limiter.enforce(
            self._bucket(principal),
            self._settings.gateway_rate_limit_per_minute,
            60,
            code="rate_limited",
            message="Rate limit exceeded. Retry after the indicated delay.",
        )
        return result.headers()

    def _validate_messages(self, payload: GatewayChatInput) -> None:
        settings = self._settings
        messages = payload.messages
        if not messages:
            raise UnprocessableEntity("At least one message is required.", code="invalid_messages")
        if len(messages) > settings.natlas_max_messages:
            raise UnprocessableEntity(
                f"A request can contain at most {settings.natlas_max_messages} messages.",
                code="invalid_messages",
            )
        for index, message in enumerate(messages):
            if message.role == "system" and index != 0:
                raise UnprocessableEntity(
                    "A system message is only allowed as the first message.",
                    code="invalid_messages",
                )
            if not message.content.strip():
                raise UnprocessableEntity(
                    "Message content cannot be empty.", code="invalid_messages"
                )
        if messages[-1].role != "user":
            raise UnprocessableEntity(
                "The last message must have the role 'user'.", code="invalid_messages"
            )
        total_chars = sum(len(message.content) for message in messages)
        if total_chars > settings.natlas_max_input_chars:
            raise UnprocessableEntity(
                f"The combined message length exceeds {settings.natlas_max_input_chars} characters.",
                code="input_too_long",
            )

    def _resolve_params(self, payload: GatewayChatInput) -> GenerationParams:
        settings = self._settings
        max_tokens = (
            payload.max_tokens if payload.max_tokens is not None else settings.natlas_default_max_tokens
        )
        if max_tokens > settings.natlas_max_tokens_limit:
            raise UnprocessableEntity(
                f"max_tokens must not exceed {settings.natlas_max_tokens_limit}.",
                code="invalid_parameters",
            )
        return GenerationParams(
            max_tokens=max_tokens,
            temperature=(
                payload.temperature
                if payload.temperature is not None
                else settings.natlas_default_temperature
            ),
            top_p=payload.top_p if payload.top_p is not None else settings.natlas_default_top_p,
            repetition_penalty=(
                payload.repetition_penalty
                if payload.repetition_penalty is not None
                else settings.natlas_default_repetition_penalty
            ),
        )

    @staticmethod
    def _map_provider_error(error: ProviderError) -> AppError:
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else {}
        return AppError(
            error.message, code=error.code, status_code=error.http_status, headers=headers
        )

    # Public API -------------------------------------------------------------------------

    async def list_models(
        self, principal: GatewayPrincipal
    ) -> tuple[list[ModelInfo], dict[str, str]]:
        self._require_scope(principal, SCOPE_MODELS_READ)
        headers = await self._enforce_rate_limit(principal)
        return await self._provider.list_models(), headers

    async def chat(
        self,
        principal: GatewayPrincipal,
        payload: GatewayChatInput,
        *,
        request_id: str,
        endpoint: str,
        method: str = "POST",
    ) -> GatewayChatOutput:
        started = time.perf_counter()
        requested_model = payload.model or self._settings.natlas_model_id
        model_supported = self._provider.supports_model(requested_model)
        rate_headers: dict[str, str] = {}
        result: ChatResult | None = None
        failure: AppError | None = None
        cause: Exception | None = None

        try:
            self._require_scope(principal, SCOPE_CHAT_CREATE)
            rate_headers = await self._enforce_rate_limit(principal)
            self._validate_messages(payload)
            if not model_supported:
                raise NotFound(
                    "The requested model is not available. List models with GET /api/v1/models.",
                    code="model_not_found",
                )
            params = self._resolve_params(payload)
            result = await self._provider.chat(
                ChatRequest(
                    model=requested_model,
                    messages=payload.messages,
                    params=params,
                    request_id=request_id,
                )
            )
        except RateLimited:
            # Throttled requests are not written to the database, which keeps logs bounded.
            raise
        except ProviderError as exc:
            cause, failure = exc, self._map_provider_error(exc)
        except AppError as exc:
            cause, failure = exc, exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        # Only supported model ids are recorded, so clients cannot create unbounded usage rows.
        recorded_model = requested_model if model_supported else None

        if failure is not None:
            annotate(error_code=failure.code)
            await self._record(
                principal,
                request_id=request_id,
                endpoint=endpoint,
                method=method,
                status_code=failure.status_code,
                outcome="error",
                error_code=failure.code,
                latency_ms=latency_ms,
                provider_latency_ms=None,
                model=recorded_model,
                language=payload.language,
                usage=None,
            )
            raise failure from cause

        if result is None:  # pragma: no cover - defensive, the try block always sets it
            raise RuntimeError("The provider returned no result.")

        await self._record(
            principal,
            request_id=request_id,
            endpoint=endpoint,
            method=method,
            status_code=200,
            outcome="success",
            error_code=None,
            latency_ms=latency_ms,
            provider_latency_ms=result.provider_latency_ms,
            model=recorded_model,
            language=payload.language,
            usage=result.usage,
        )
        return GatewayChatOutput(
            request_id=request_id,
            model=result.model,
            text=result.text,
            finish_reason=result.finish_reason,
            usage=result.usage,
            latency_ms=latency_ms,
            rate_limit_headers=rate_headers,
        )

    async def _record(
        self,
        principal: GatewayPrincipal,
        *,
        request_id: str,
        endpoint: str,
        method: str,
        status_code: int,
        outcome: str,
        error_code: str | None,
        latency_ms: int,
        provider_latency_ms: int | None,
        model: str | None,
        language: str | None,
        usage: Usage | None,
    ) -> None:
        """Write the request log and usage counters. Telemetry failures never fail the caller."""
        now = utcnow()
        try:
            async with self._session_factory() as session:
                session.add(
                    RequestLog(
                        request_id=request_id,
                        project_id=principal.project_id,
                        api_key_id=principal.api_key_id,
                        user_id=principal.user_id,
                        source=principal.source,
                        endpoint=endpoint[:120],
                        method=method[:8],
                        status_code=status_code,
                        outcome=outcome,
                        error_code=error_code,
                        latency_ms=latency_ms,
                        provider_latency_ms=provider_latency_ms,
                        model=model,
                        language=language,
                        prompt_tokens=usage.prompt_tokens if usage else None,
                        completion_tokens=usage.completion_tokens if usage else None,
                        stream=False,
                    )
                )
                if model is not None:
                    insert_statement = pg_insert(UsageDaily).values(
                        project_id=principal.project_id,
                        day=now.date(),
                        model=model,
                        request_count=1,
                        success_count=1 if outcome == "success" else 0,
                        error_count=0 if outcome == "success" else 1,
                        prompt_tokens=usage.prompt_tokens if usage else 0,
                        completion_tokens=usage.completion_tokens if usage else 0,
                        updated_at=now,
                    )
                    excluded = insert_statement.excluded
                    await session.execute(
                        insert_statement.on_conflict_do_update(
                            index_elements=[UsageDaily.project_id, UsageDaily.day, UsageDaily.model],
                            set_={
                                "request_count": UsageDaily.request_count + excluded.request_count,
                                "success_count": UsageDaily.success_count + excluded.success_count,
                                "error_count": UsageDaily.error_count + excluded.error_count,
                                "prompt_tokens": UsageDaily.prompt_tokens + excluded.prompt_tokens,
                                "completion_tokens": (
                                    UsageDaily.completion_tokens + excluded.completion_tokens
                                ),
                                "updated_at": excluded.updated_at,
                            },
                        )
                    )
                await session.commit()
        except Exception:
            log.exception("telemetry.write_failed")
