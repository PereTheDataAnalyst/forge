from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1, max_length=200)
    max_new_tokens: int = Field(default=512, ge=1, le=8192)
    temperature: float = Field(default=0.1, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    repetition_penalty: float = Field(default=1.12, ge=1, le=2)


class ChatResponse(BaseModel):
    text: str
    finish_reason: Literal["stop", "length"]
    prompt_tokens: int
    completion_tokens: int
    model_id: str
    generation_ms: int
