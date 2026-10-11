"""N-ATLAS provider.

N-ATLAS is distributed as open weights on Hugging Face (`NCAIR1/N-ATLaS`) and the official
usage is the Transformers library with the model's chat template. Forge therefore talks to a
small inference worker (see inference/natlas-worker) that wraps exactly that usage and exposes
an authenticated JSON API on a private address.

Security properties of this client:
  * The destination is fixed by configuration. No user input ever influences the URL.
  * Redirects are disabled and proxy environment variables are ignored.
  * Responses are validated field by field before use.
  * Credentials are sent only in the Authorization header and are never logged.

When official N-ATLAS API credentials are issued (the programme provides them to shortlisted
teams) and their interface is published, add a second ModelProvider implementation instead of
changing this one.
"""

import asyncio
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.providers import natlas_catalog as catalog
from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ModelProvider,
    ModelStatus,
    ProviderBusy,
    ProviderError,
    ProviderHealth,
    ProviderLoading,
    ProviderMisconfigured,
    ProviderNotConfigured,
    ProviderProtocolError,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)

log = get_logger(__name__)

_REJECTION_CODES = frozenset({"context_length_exceeded", "invalid_request"})
_HEALTH_PROBE_TIMEOUT = httpx.Timeout(3.0)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _safe_message(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else ""
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return min(max(seconds, 1), 300)


class NAtlasProvider(ModelProvider):
    name = "natlas"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._health_cache: tuple[float, ProviderHealth] | None = None
        self._health_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self._settings.natlas_configured

    async def startup(self) -> None:
        if not self.configured:
            log.warning(
                "natlas.provider_not_configured",
                extra={"hint": "Set NATLAS_INFERENCE_URL and NATLAS_INFERENCE_TOKEN"},
            )
            return
        token = self._settings.natlas_inference_token.get_secret_value()  # type: ignore[union-attr]
        self._client = httpx.AsyncClient(
            base_url=self._settings.natlas_inference_url or "",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "natlas-forge-gateway/0.1",
            },
            timeout=httpx.Timeout(
                connect=self._settings.natlas_connect_timeout_seconds,
                read=self._settings.natlas_request_timeout_seconds,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            trust_env=False,
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # Health -----------------------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        model_id = self._settings.natlas_model_id
        if not self.configured:
            return ProviderHealth(
                "not_configured",
                model_id,
                "N-ATLAS inference is not configured. Set NATLAS_INFERENCE_URL and "
                "NATLAS_INFERENCE_TOKEN to connect the inference worker.",
            )
        ttl = self._settings.natlas_health_cache_seconds
        cached = self._health_cache
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        async with self._health_lock:
            cached = self._health_cache
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
            health = await self._probe()
            self._health_cache = (time.monotonic(), health)
            return health

    async def _probe(self) -> ProviderHealth:
        model_id = self._settings.natlas_model_id
        if self._client is None:
            return ProviderHealth("error", model_id, "The provider client has not started.")
        try:
            response = await self._client.get("/v1/info", timeout=_HEALTH_PROBE_TIMEOUT)
        except httpx.TimeoutException:
            return ProviderHealth("unreachable", model_id, "The inference service timed out.")
        except httpx.HTTPError:
            return ProviderHealth("unreachable", model_id, "The inference service is not reachable.")
        if response.status_code in (401, 403):
            return ProviderHealth(
                "error", model_id, "The inference service rejected the gateway credentials."
            )
        if response.status_code != 200:
            return ProviderHealth(
                "error", model_id, f"The inference service answered with HTTP {response.status_code}."
            )
        try:
            body = response.json()
        except ValueError:
            return ProviderHealth("error", model_id, "The inference service sent invalid JSON.")
        state = body.get("state") if isinstance(body, dict) else None
        reported = body.get("model_id") if isinstance(body, dict) else None
        reported_id = reported if isinstance(reported, str) else model_id
        if state == "ready":
            return ProviderHealth("ok", reported_id)
        if state == "loading":
            return ProviderHealth("loading", reported_id, "The model is loading into memory.")
        if state == "failed":
            return ProviderHealth(
                "error", reported_id, "The model failed to load. Check the worker log."
            )
        return ProviderHealth("error", reported_id, "The inference service reported an unknown state.")

    # Models -----------------------------------------------------------------------------

    def supports_model(self, model_id: str) -> bool:
        return model_id == self._settings.natlas_model_id

    async def list_models(self) -> list[ModelInfo]:
        health = await self.health()
        status: ModelStatus = {
            "ok": "available",
            "loading": "loading",
            "not_configured": "not_configured",
        }.get(health.status, "unavailable")  # type: ignore[assignment]
        return [
            ModelInfo(
                id=self._settings.natlas_model_id,
                kind="llm",
                display_name="N-ATLaS LLM",
                architecture="LlamaForCausalLM (Llama-3 8B base)",
                parameters="8B",
                languages=catalog.LLM_LANGUAGES,
                context_length_tokens=catalog.LLM_CONTEXT_LENGTH_TOKENS,
                license=catalog.LICENSE_NAME,
                source_url=catalog.LLM_SOURCE_URL,
                attribution=catalog.LLM_ATTRIBUTION,
                status=status,
            )
        ]

    # Chat -------------------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResult:
        if not self.configured or self._client is None:
            raise ProviderNotConfigured()
        params = request.params
        payload = {
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_new_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "repetition_penalty": params.repetition_penalty,
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(
                "/v1/chat", json=payload, headers={"X-Request-ID": request.request_id}
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout() from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable() from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            raise self._error_from_response(response)
        return self._parse_result(response, request.model, elapsed_ms)

    @staticmethod
    def _error_from_response(response: httpx.Response) -> ProviderError:
        code: str | None = None
        message: str | None = None
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = error.get("code") if isinstance(error.get("code"), str) else None
                message = _safe_message(error.get("message")) or None
        except ValueError:
            pass
        retry_after = _parse_retry_after(response.headers.get("retry-after"))
        status = response.status_code

        if code == "model_loading":
            return ProviderLoading(retry_after=retry_after)
        if code == "busy" or status == 429:
            return ProviderBusy(retry_after=retry_after)
        if code in _REJECTION_CODES and status in (400, 422):
            return ProviderRejected(message, rejection_code=code)
        if status in (401, 403):
            return ProviderMisconfigured()
        if code == "model_failed" or status == 503:
            return ProviderUnavailable(retry_after=retry_after)
        return ProviderError()

    @staticmethod
    def _parse_result(response: httpx.Response, requested_model: str, elapsed_ms: int) -> ChatResult:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderProtocolError() from exc
        if not isinstance(body, dict):
            raise ProviderProtocolError()
        text = body.get("text")
        finish = body.get("finish_reason")
        prompt_tokens = body.get("prompt_tokens")
        completion_tokens = body.get("completion_tokens")
        if (
            not isinstance(text, str)
            or finish not in ("stop", "length")
            or not _is_count(prompt_tokens)
            or not _is_count(completion_tokens)
        ):
            raise ProviderProtocolError()
        reported = body.get("model_id")
        return ChatResult(
            model=reported if isinstance(reported, str) and reported else requested_model,
            text=text,
            finish_reason=finish,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            provider_latency_ms=elapsed_ms,
        )
