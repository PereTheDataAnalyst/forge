"""Transcription for the platform: limits, the library pipeline, and metadata-only records.

A request is admitted after authentication and the per-caller rate limit. From then on every
outcome, success or failure, is recorded through the recorder: in `requests` (so it shows in
request logs and charts) and in `voice_requests`. Neither table holds audio or a transcript:
only sizes, durations, timings, the format, the model and an error code.

This module does not touch the database. The recorder is injected, so the logic here can be
tested on its own. `records.DbVoiceRecorder` is the real recorder.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from forge_integrations.errors import ForgeError
from forge_integrations.natlas_facts import AsrFacts
from forge_integrations.voice import VoiceFailure, VoicePipeline, resolve_asr_language
from forge_integrations.voice.pipeline import StepRecord, VoiceResult

from app.core.errors import AppError, ServiceUnavailable
from app.core.logging import get_logger
from app.modules.voice.errors import to_app_error
from app.modules.voice.runtime import VoiceRuntime

if TYPE_CHECKING:
    from app.cache.rate_limit import RateLimiter
    from app.core.config import Settings

API_ENDPOINT = "/api/v1/audio/transcriptions"
BROWSER_ENDPOINT = "/api/v1/voice/transcriptions"
RATE_WINDOW_SECONDS = 60

log = get_logger("forge.voice")


@dataclass(frozen=True, slots=True)
class VoiceRecord:
    """Everything stored about one transcription request. Never audio, never a transcript."""

    request_id: str
    project_id: uuid.UUID
    api_key_id: uuid.UUID | None
    user_id: uuid.UUID | None
    endpoint: str
    model: str
    language: str
    status_code: int
    outcome: str
    error_code: str | None
    latency_ms: int
    validation_ms: int | None
    asr_ms: int | None
    audio_format: str | None
    audio_bytes: int
    audio_duration_ms: int | None
    transcript_chars: int | None


class VoiceRecorder(Protocol):
    """Stores a `VoiceRecord`. Must never raise: telemetry cannot fail a request."""

    async def __call__(self, record: VoiceRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class VoiceCaller:
    """Who is calling. A project API key, or a signed-in person using the browser page."""

    project_id: uuid.UUID
    api_key_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    endpoint: str = API_ENDPOINT

    @property
    def bucket(self) -> str:
        if self.api_key_id is not None:
            return f"rl:voice:key:{self.api_key_id}"
        if self.user_id is not None:
            return f"rl:voice:user:{self.user_id}"
        return f"rl:voice:project:{self.project_id}"


@dataclass(frozen=True, slots=True)
class VoiceOutcome:
    request_id: str
    language: str
    model: str
    text: str
    audio_format: str
    audio_bytes: int
    duration_seconds: float | None
    latency_ms: int
    attribution: str
    steps: list[dict[str, Any]]
    headers: dict[str, str]


class LogObserver:
    """Writes the library's pipeline events to the application log.

    The library already keeps transcripts, audio and secrets out of its events. Keys are
    prefixed so none can collide with a reserved logging attribute.
    """

    def __init__(self) -> None:
        self._log = get_logger("forge.voice.pipeline")

    def emit(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("event", "voice.event"))[:60]
        extra = {f"voice_{key}": value for key, value in event.items() if key != "event"}
        self._log.info(name, extra=extra)


def resolve_language(value: str) -> AsrFacts:
    """The official ASR model for a language code or name, or a 422 that lists the options."""
    try:
        return resolve_asr_language(value)
    except ForgeError as exc:
        raise to_app_error(exc) from None


def _step_ms(steps: list[StepRecord], name: str) -> int | None:
    for step in steps:
        if step.name == name:
            return int(step.duration_ms + 0.5)
    return None


# Declared content types that name each supported format. Only these names are ever stored, so a
# failed upload is recorded in the same vocabulary as a successful one, and text sent by a
# client never ends up in a column.
_FORMAT_BY_MIME = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/ogg": "ogg",
    "application/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mp4": "mp4",
    "audio/m4a": "mp4",
    "audio/x-m4a": "mp4",
    "video/mp4": "mp4",
    "audio/webm": "webm",
    "video/webm": "webm",
}


def _format_name(declared_mime: str | None) -> str | None:
    """The format a declared content type names, or None when it names none we support."""
    if not declared_mime:
        return None
    return _FORMAT_BY_MIME.get(declared_mime.split(";", 1)[0].strip().lower())


class VoiceService:
    def __init__(
        self,
        *,
        settings: Settings,
        runtime: VoiceRuntime,
        limiter: RateLimiter,
        recorder: VoiceRecorder,
        observer: LogObserver | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._limiter = limiter
        self._recorder = recorder
        self._observer = observer or LogObserver()

    async def transcribe(
        self,
        caller: VoiceCaller,
        *,
        audio: bytes,
        declared_mime: str | None,
        language: str,
        request_id: str,
    ) -> VoiceOutcome:
        facts = resolve_language(language)
        admitted = await self._limiter.enforce(
            caller.bucket,
            self._settings.voice_rate_limit_per_minute,
            RATE_WINDOW_SECONDS,
            code="rate_limited",
            message="Too many audio requests. Retry after the indicated delay.",
        )
        rate_headers = admitted.headers()

        started = time.perf_counter()
        result: VoiceResult | None = None
        steps: list[StepRecord] = []
        failure: AppError | None = None

        provider = await self._runtime.get_provider()
        if provider is None:
            if self._runtime.error is None:
                failure = ServiceUnavailable(
                    "No speech recognition worker is connected to this deployment.",
                    code="provider_not_configured",
                )
            else:
                failure = ServiceUnavailable(
                    "The speech recognition worker cannot be reached from this deployment.",
                    code="provider_unavailable",
                )
        elif not self._runtime.try_acquire():
            failure = ServiceUnavailable(
                "The speech service is handling other uploads. Try again shortly.",
                code="provider_busy",
                headers={"Retry-After": "2"},
            )
        else:
            try:
                pipeline = VoicePipeline(
                    provider,
                    max_upload_bytes=self._settings.voice_max_upload_bytes,
                    max_seconds=self._settings.voice_max_seconds,
                    observer=self._observer,
                )
                result = await pipeline.transcribe(
                    audio,
                    language=facts.language,
                    declared_mime=declared_mime,
                    project_id=str(caller.project_id),
                    request_id=request_id,
                )
                steps = result.steps
            except VoiceFailure as exc:
                failure = to_app_error(exc)
                steps = exc.steps
            except ForgeError as exc:
                failure = to_app_error(exc)
            except Exception:
                log.exception("voice.unexpected_error")
                failure = AppError(
                    "Speech transcription failed unexpectedly.",
                    code="internal_error",
                    status_code=500,
                )
            finally:
                self._runtime.release()

        latency_ms = int((time.perf_counter() - started) * 1000)
        await self._record(
            caller,
            request_id=request_id,
            facts=facts,
            latency_ms=latency_ms,
            audio_size=len(audio),
            declared_mime=declared_mime,
            result=result,
            steps=steps,
            failure=failure,
        )
        if failure is not None or result is None:
            error = failure or AppError(code="internal_error", status_code=500)
            error.headers = {**rate_headers, **error.headers}
            raise error

        return VoiceOutcome(
            request_id=request_id,
            language=facts.language,
            model=result.transcription.model or facts.repo_id,
            text=result.transcription.text,
            audio_format=result.audio.format.value,
            audio_bytes=result.audio.size_bytes,
            duration_seconds=result.transcription.duration_seconds
            or result.audio.duration_seconds,
            latency_ms=latency_ms,
            attribution=facts.required_attribution,
            steps=[
                {
                    "name": step.name,
                    "status": step.status,
                    "duration_ms": int(step.duration_ms + 0.5),
                }
                for step in steps
            ],
            headers=rate_headers,
        )

    async def _record(
        self,
        caller: VoiceCaller,
        *,
        request_id: str,
        facts: AsrFacts,
        latency_ms: int,
        audio_size: int,
        declared_mime: str | None,
        result: VoiceResult | None,
        steps: list[StepRecord],
        failure: AppError | None,
    ) -> None:
        succeeded = failure is None and result is not None
        error_code = None if succeeded else (failure.code if failure else "internal_error")
        duration_ms = None
        audio_format = _format_name(declared_mime)
        if result is not None:
            seconds = result.transcription.duration_seconds or result.audio.duration_seconds
            duration_ms = int(seconds * 1000) if seconds else None
            audio_format = result.audio.format.value
        record = VoiceRecord(
            request_id=request_id,
            project_id=caller.project_id,
            api_key_id=caller.api_key_id,
            user_id=caller.user_id,
            endpoint=caller.endpoint,
            model=facts.repo_id,
            language=facts.language,
            status_code=200 if succeeded else (failure.status_code if failure else 500),
            outcome="success" if succeeded else "error",
            error_code=error_code[:64] if error_code else None,
            latency_ms=latency_ms,
            validation_ms=_step_ms(steps, "validate"),
            asr_ms=_step_ms(steps, "asr"),
            audio_format=audio_format,
            audio_bytes=audio_size,
            audio_duration_ms=duration_ms,
            transcript_chars=len(result.transcription.text) if result else None,
        )
        try:
            await self._recorder(record)
        except Exception:
            log.exception("telemetry.write_failed")
