"""Voice pipeline: validate -> N-ATLAS ASR -> (optionally) N-ATLAS LLM.

Every step is timed and recorded, on success and on failure, so the whole chain is
observable. Audio bytes and transcript text are never placed in events.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import ErrorCode, ForgeError
from ..ports import AsrProvider, ChatMessage, GenerationRequest, GenerationResult, ModelGateway, Observer, TranscriptionResult
from .audio import DEFAULT_MAX_BYTES, DEFAULT_MAX_SECONDS, ValidatedAudio, validate_audio_upload
from .languages import resolve_asr_language

MAX_TRANSCRIPT_TO_LLM_CHARS = 4000


@dataclass
class StepRecord:
    name: str  # "validate" | "asr" | "llm"
    status: str  # "ok" | "error"
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "detail": self.detail,
            "error_code": self.error_code,
        }


@dataclass
class VoiceResult:
    request_id: str
    language: str
    audio: ValidatedAudio
    transcription: TranscriptionResult
    response: GenerationResult | None
    steps: list[StepRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "language": self.language,
            "audio": {
                "format": self.audio.format.value,
                "size_bytes": self.audio.size_bytes,
                "sha256": self.audio.sha256,
                "duration_seconds": self.transcription.duration_seconds or self.audio.duration_seconds,
            },
            "transcription": {
                "text": self.transcription.text,
                "model": self.transcription.model,
                "latency_ms": self.transcription.latency_ms,
            },
            "response": None
            if self.response is None
            else {
                "text": self.response.text,
                "model": self.response.model,
                "finish_reason": self.response.finish_reason,
                "usage": None
                if self.response.usage is None
                else {
                    "prompt_tokens": self.response.usage.prompt_tokens,
                    "completion_tokens": self.response.usage.completion_tokens,
                    "total_tokens": self.response.usage.total_tokens,
                },
            },
            "steps": [s.to_dict() for s in self.steps],
        }


class VoiceFailure(ForgeError):
    """A pipeline failure that keeps the recorded steps for logging and persistence."""

    def __init__(self, cause: ForgeError, request_id: str, steps: list[StepRecord]) -> None:
        super().__init__(
            cause.code,
            cause.message,
            http_status=cause.http_status,
            retryable=cause.retryable,
            details=cause.details,
            headers=cause.headers,
        )
        self.request_id = request_id
        self.steps = steps


class VoicePipeline:
    def __init__(
        self,
        asr: AsrProvider,
        *,
        gateway: ModelGateway | None = None,
        max_upload_bytes: int = DEFAULT_MAX_BYTES,
        max_seconds: int = DEFAULT_MAX_SECONDS,
        observer: Observer | None = None,
        perf: Callable[[], float] = time.perf_counter,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._asr = asr
        self._gateway = gateway
        self._max_bytes = max_upload_bytes
        self._max_seconds = max_seconds
        self._observer = observer
        self._perf = perf
        self._new_id = new_id

    def _emit(self, event: dict[str, Any]) -> None:
        if self._observer is not None:
            try:
                self._observer.emit(event)
            except Exception:  # noqa: BLE001 - observability must never break a request
                pass

    async def transcribe(
        self,
        audio: bytes,
        *,
        language: str,
        declared_mime: str | None = None,
        project_id: str | None = None,
        request_id: str | None = None,
    ) -> VoiceResult:
        return await self._run(audio, language, declared_mime, project_id, request_id, respond=False)

    async def transcribe_and_respond(
        self,
        audio: bytes,
        *,
        language: str,
        declared_mime: str | None = None,
        system_prompt: str | None = None,
        respond_language: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        project_id: str | None = None,
        request_id: str | None = None,
    ) -> VoiceResult:
        if self._gateway is None:
            raise ForgeError(
                ErrorCode.PROVIDER_NOT_CONFIGURED,
                "No N-ATLAS text generation provider is configured for the voice pipeline.",
                http_status=503,
            )
        return await self._run(
            audio, language, declared_mime, project_id, request_id,
            respond=True, system_prompt=system_prompt, respond_language=respond_language,
            model=model, temperature=temperature, max_tokens=max_tokens,
        )

    async def _run(
        self,
        audio: bytes,
        language: str,
        declared_mime: str | None,
        project_id: str | None,
        request_id: str | None,
        *,
        respond: bool,
        system_prompt: str | None = None,
        respond_language: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VoiceResult:
        rid = request_id or self._new_id()
        steps: list[StepRecord] = []

        def record(name: str, started: float, error: ForgeError | None = None, **detail: Any) -> None:
            step = StepRecord(
                name=name,
                status="error" if error else "ok",
                duration_ms=(self._perf() - started) * 1000,
                detail=detail,
                error_code=error.code if error else None,
            )
            steps.append(step)
            self._emit(
                {
                    "event": "voice.step",
                    "request_id": rid,
                    "project_id": project_id,
                    "step": name,
                    "status": step.status,
                    "duration_ms": round(step.duration_ms, 2),
                    "error_code": step.error_code,
                    **{k: v for k, v in detail.items() if k in {"format", "size_bytes", "language", "model"}},
                }
            )

        # 1. validate
        started = self._perf()
        try:
            facts = resolve_asr_language(language)
            validated = validate_audio_upload(audio, declared_mime, max_bytes=self._max_bytes, max_seconds=self._max_seconds)
        except ForgeError as exc:
            record("validate", started, exc)
            raise VoiceFailure(exc, rid, steps) from None
        record("validate", started, format=validated.format.value, size_bytes=validated.size_bytes, language=facts.language)

        # 2. N-ATLAS ASR
        started = self._perf()
        try:
            transcription = await self._asr.transcribe(
                audio=audio, audio_format=validated.format.value, language=facts.language, request_id=rid
            )
        except ForgeError as exc:
            record("asr", started, exc, language=facts.language, model=facts.repo_id)
            raise VoiceFailure(exc, rid, steps) from None
        record("asr", started, language=facts.language, model=transcription.model)

        # 3. N-ATLAS LLM, only when requested
        response: GenerationResult | None = None
        if respond:
            started = self._perf()
            messages: list[ChatMessage] = []
            if system_prompt:
                messages.append(ChatMessage("system", system_prompt))
            messages.append(ChatMessage("user", transcription.text[:MAX_TRANSCRIPT_TO_LLM_CHARS]))
            try:
                assert self._gateway is not None
                response = await self._gateway.generate(
                    GenerationRequest(
                        messages=tuple(messages),
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        language=respond_language or facts.language,
                        request_id=rid,
                        project_id=project_id,
                        source="voice",
                    )
                )
            except ForgeError as exc:
                record("llm", started, exc)
                raise VoiceFailure(exc, rid, steps) from None
            record("llm", started, model=response.model or model or "")

        return VoiceResult(
            request_id=rid,
            language=facts.language,
            audio=validated,
            transcription=transcription,
            response=response,
            steps=steps,
        )
