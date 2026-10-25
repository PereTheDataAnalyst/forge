"""Request and response shapes for the Voice module."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TranscriptionAudioOut(BaseModel):
    format: str
    size_bytes: int
    duration_seconds: float | None


class TranscriptionOut(BaseModel):
    request_id: str
    language: str
    model: str
    text: str
    audio: TranscriptionAudioOut
    latency_ms: int
    attribution: str


class VoiceStepOut(BaseModel):
    name: str
    status: str
    duration_ms: int


class BrowserTranscriptionOut(TranscriptionOut):
    steps: list[VoiceStepOut]


class VoiceLanguageOut(BaseModel):
    code: str
    model: str
    model_name: str
    architecture: str
    parameters: str
    attribution: str
    source: str


class VoiceLimitsOut(BaseModel):
    max_upload_bytes: int
    max_seconds: int
    formats: list[str]


class VoiceStatusOut(BaseModel):
    state: Literal["ready", "not_configured", "misconfigured"]
    configured: bool
    message: str
    limits: VoiceLimitsOut
    languages: list[VoiceLanguageOut]


class VoiceRequestOut(BaseModel):
    id: uuid.UUID
    request_id: str
    created_at: datetime
    via: Literal["api_key", "browser"]
    language: str
    asr_model: str
    audio_format: str | None
    audio_bytes: int | None
    audio_duration_ms: int | None
    status: str
    validation_ms: int | None
    asr_ms: int | None
    transcript_chars: int | None
    error_code: str | None


class VoiceRequestListOut(BaseModel):
    data: list[VoiceRequestOut]
