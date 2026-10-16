"""A scripted model provider for integration tests. It exists only in tests.

It replaces the N-ATLAS worker behind a real `GatewayService`, so routes, authentication,
scopes, rate limits, request logging and usage metering run for real while no network call is
made. Runtime code never imports this module.
"""

from fastapi import FastAPI

from app.providers.base import (
    ChatRequest,
    ChatResult,
    ModelInfo,
    ModelProvider,
    ProviderHealth,
    Usage,
)
from app.services.gateway_service import GatewayService

MODEL_ID = "NCAIR1/N-ATLaS"


class ScriptedProvider(ModelProvider):
    name = "scripted"

    def __init__(self) -> None:
        self.calls: list[ChatRequest] = []
        # Items are returned (or raised, for exceptions) in order. When empty, a default reply
        # is returned.
        self.replies: list[ChatResult | Exception] = []

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(status="ok", model_id=MODEL_ID)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=MODEL_ID,
                kind="llm",
                display_name="N-ATLaS LLM",
                architecture="Llama-3 8B (LlamaForCausalLM)",
                parameters="8B",
                languages=("en", "ha", "ig", "yo"),
                context_length_tokens=8092,
                license="N-ATLaS Terms of Use",
                source_url="https://huggingface.co/NCAIR1/N-ATLaS",
                attribution="Test attribution text.",
                status="available",
            ),
            ModelInfo(
                id="NCAIR1/Yoruba-ASR",
                kind="asr",
                display_name="Yoruba ASR",
                architecture=None,
                parameters=None,
                languages=("yo",),
                context_length_tokens=None,
                license="N-ATLaS Terms of Use",
                source_url="https://huggingface.co/NCAIR1/Yoruba-ASR",
                attribution=None,
                status="not_configured",
            ),
        ]

    def supports_model(self, model_id: str) -> bool:
        return model_id == MODEL_ID

    async def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        if self.replies:
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return ChatResult(
            model=request.model,
            text="Scripted reply",
            finish_reason="stop",
            usage=Usage(prompt_tokens=7, completion_tokens=3),
            provider_latency_ms=12,
        )


def install_scripted_provider(app: FastAPI) -> ScriptedProvider:
    """Swap the running app's gateway for one that uses a scripted provider.

    Call it after the application has started (inside `with TestClient(app)`). Routes read
    `app.state.gateway` on every request, so the swap takes effect immediately.
    """
    provider = ScriptedProvider()
    app.state.gateway = GatewayService(
        settings=app.state.settings,
        provider=provider,
        limiter=app.state.rate_limiter,
        session_factory=app.state.session_factory,
    )
    return provider
