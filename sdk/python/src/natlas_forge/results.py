"""Typed results. Every result that comes from the server has a ``request_id``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class RateLimit:
    """The limit headers of a response. Any field may be None if the server did not send it."""

    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_seconds: Optional[int] = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatCompletion:
    request_id: str
    model: str
    message: ChatMessage
    finish_reason: str
    usage: Usage
    latency_ms: int
    rate_limit: RateLimit

    @property
    def text(self) -> str:
        """The assistant's reply."""
        return self.message.content


@dataclass(frozen=True)
class Model:
    id: str
    kind: str
    display_name: str
    architecture: Optional[str]
    parameters: Optional[str]
    languages: Tuple[str, ...]
    context_length_tokens: Optional[int]
    license: str
    source_url: str
    attribution: Optional[str]
    status: str


@dataclass(frozen=True)
class ModelList:
    request_id: str
    data: Tuple[Model, ...]
    rate_limit: RateLimit


@dataclass(frozen=True)
class TranscriptionAudio:
    format: str
    size_bytes: int
    duration_seconds: Optional[float]


@dataclass(frozen=True)
class Transcription:
    request_id: str
    language: str
    model: str
    text: str
    audio: TranscriptionAudio
    latency_ms: int
    attribution: str
    rate_limit: RateLimit
