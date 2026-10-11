"""Provider abstractions.

`ModelProvider` is implemented by `NAtlasProvider`. The abstraction keeps the platform
independent of how N-ATLAS is served, so the transport can change (for example when official
API credentials become available) without touching the gateway, SDKs or dashboards.
No fake or mock provider exists in the runtime code path.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Role = Literal["system", "user", "assistant"]
ModelKind = Literal["llm", "asr"]
ModelStatus = Literal["available", "unavailable", "not_configured", "loading"]
ProviderStatus = Literal["ok", "not_configured", "unreachable", "loading", "error"]
FinishReason = Literal["stop", "length"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class GenerationParams:
    max_tokens: int
    temperature: float
    top_p: float
    repetition_penalty: float


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    params: GenerationParams
    request_id: str


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class ChatResult:
    model: str
    text: str
    finish_reason: FinishReason
    usage: Usage
    provider_latency_ms: int


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    kind: ModelKind
    display_name: str
    architecture: str | None
    parameters: str | None
    languages: tuple[str, ...]
    context_length_tokens: int | None
    license: str
    source_url: str
    attribution: str | None
    status: ModelStatus


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    status: ProviderStatus
    model_id: str | None
    detail: str | None = None


# Interfaces for the voice module (implemented by Agent 2) ------------------------------


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    model: str
    language: str
    audio_path: Path
    content_type: str
    request_id: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    model: str
    language: str
    text: str
    provider_latency_ms: int
    audio_duration_ms: int | None


# Errors -------------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class. `code` and `http_status` are mapped to API errors by the gateway."""

    code = "provider_error"
    http_status = 502
    message = "The N-ATLAS inference service returned an error."

    def __init__(self, message: str | None = None, *, retry_after: int | None = None) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        self.retry_after = retry_after


class ProviderNotConfigured(ProviderError):
    code = "provider_not_configured"
    http_status = 503
    message = "N-ATLAS inference is not configured on this deployment."


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    http_status = 503
    message = "The N-ATLAS inference service is not reachable. Try again shortly."


class ProviderLoading(ProviderError):
    code = "provider_loading"
    http_status = 503
    message = "The N-ATLAS model is still loading. Try again shortly."


class ProviderBusy(ProviderError):
    code = "provider_busy"
    http_status = 503
    message = "The N-ATLAS inference service is at capacity. Retry after a short delay."


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    http_status = 504
    message = "The N-ATLAS inference service did not respond in time."


class ProviderMisconfigured(ProviderError):
    code = "provider_misconfigured"
    http_status = 502
    message = "The gateway credentials were rejected by the inference service."


class ProviderProtocolError(ProviderError):
    code = "provider_invalid_response"
    http_status = 502
    message = "The inference service returned a response the gateway could not understand."


class ProviderRejected(ProviderError):
    """The inference service rejected the request as invalid (for example context too long)."""

    code = "provider_rejected"
    http_status = 422
    message = "The inference service rejected the request."

    def __init__(self, message: str | None = None, *, rejection_code: str = "provider_rejected"):
        super().__init__(message)
        self.code = rejection_code


# Provider interfaces ------------------------------------------------------------------


class ModelProvider(ABC):
    name: str

    @abstractmethod
    async def startup(self) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def supports_model(self, model_id: str) -> bool: ...

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResult: ...


class AsrProvider(ABC):
    """Implemented by the voice module. Register an instance on `app.state.integrations`."""

    name: str

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...
