from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["en", "ha", "ig", "yo"]


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=120)
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=100)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    repetition_penalty: float | None = Field(default=None, ge=1, le=2)
    # Recorded in request logs and metrics. It does not change model behaviour.
    language: Language | None = None


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatMessageOut(BaseModel):
    role: Literal["assistant"]
    content: str


class ChatResponseOut(BaseModel):
    request_id: str
    model: str
    message: ChatMessageOut
    finish_reason: Literal["stop", "length"]
    usage: UsageOut
    latency_ms: int


class ModelOut(BaseModel):
    id: str
    kind: Literal["llm", "asr"]
    display_name: str
    architecture: str | None
    parameters: str | None
    languages: list[str]
    context_length_tokens: int | None
    license: str
    source_url: str
    attribution: str | None
    status: Literal["available", "unavailable", "not_configured", "loading"]


class ModelListOut(BaseModel):
    data: list[ModelOut]
