"""Voice endpoints: a developer API (project API key) and the browser page (cookie session)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from forge_integrations.natlas_facts import ASR_MODELS
from forge_integrations.voice import AudioFormat
from sqlalchemy import select

from app.api.deps import AuthDep, DbDep, PrincipalDep
from app.core.errors import AppError, Forbidden
from app.core.request_context import get_request_id
from app.db.models.integration import VoiceRequest
from app.modules.voice.records import DbVoiceRecorder
from app.modules.voice.runtime import SCOPE_ASR_TRANSCRIBE, VoiceRuntime
from app.modules.voice.schemas import (
    BrowserTranscriptionOut,
    TranscriptionAudioOut,
    TranscriptionOut,
    VoiceLanguageOut,
    VoiceLimitsOut,
    VoiceRequestListOut,
    VoiceRequestOut,
    VoiceStatusOut,
    VoiceStepOut,
)
from app.modules.voice.service import (
    API_ENDPOINT,
    BROWSER_ENDPOINT,
    VoiceCaller,
    VoiceOutcome,
    VoiceService,
)
from app.schemas.common import ERROR_RESPONSES
from app.services import project_service

router = APIRouter(tags=["voice"])

# Content types that mean the caller sent a form or JSON instead of the audio file itself.
_NOT_AUDIO_PREFIXES = ("multipart/", "application/json", "application/x-www-form-urlencoded")


def get_voice_service(request: Request) -> VoiceService:
    state = request.app.state
    return VoiceService(
        settings=state.settings,
        runtime=state.voice,
        limiter=state.rate_limiter,
        recorder=DbVoiceRecorder(state.session_factory),
    )


VoiceDep = Annotated[VoiceService, Depends(get_voice_service)]
LanguageQuery = Annotated[str, Query(min_length=2, max_length=32)]


def _request_id() -> str:
    return get_request_id() or uuid.uuid4().hex


async def read_audio(request: Request) -> tuple[bytes, str | None]:
    """The raw request body and its Content-Type. The body is the audio file, nothing else."""
    content_type = request.headers.get("content-type")
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if base.startswith(_NOT_AUDIO_PREFIXES):
        raise AppError(
            "Send the audio file as the raw request body with its own Content-Type, "
            "not as a form or as JSON.",
            code="unsupported_media_type",
            status_code=415,
        )
    return await request.body(), content_type


def _audio(outcome: VoiceOutcome) -> TranscriptionAudioOut:
    return TranscriptionAudioOut(
        format=outcome.audio_format,
        size_bytes=outcome.audio_bytes,
        duration_seconds=outcome.duration_seconds,
    )


@router.post("/audio/transcriptions", response_model=TranscriptionOut, responses=ERROR_RESPONSES)
async def transcribe_with_key(
    request: Request,
    response: Response,
    principal: PrincipalDep,
    voice: VoiceDep,
    language: LanguageQuery,
) -> TranscriptionOut:
    """Transcribe speech. The body is the audio file. Requires the asr.transcribe scope."""
    if not principal.has_scope(SCOPE_ASR_TRANSCRIBE):
        raise Forbidden(
            f"This credential does not have the '{SCOPE_ASR_TRANSCRIBE}' scope.",
            code="insufficient_scope",
        )
    audio, content_type = await read_audio(request)
    outcome = await voice.transcribe(
        VoiceCaller(
            project_id=principal.project_id,
            api_key_id=principal.api_key_id,
            endpoint=API_ENDPOINT,
        ),
        audio=audio,
        declared_mime=content_type,
        language=language,
        request_id=_request_id(),
    )
    response.headers.update(outcome.headers)
    return TranscriptionOut(
        request_id=outcome.request_id,
        language=outcome.language,
        model=outcome.model,
        text=outcome.text,
        audio=_audio(outcome),
        latency_ms=outcome.latency_ms,
        attribution=outcome.attribution,
    )


@router.post(
    "/voice/transcriptions", response_model=BrowserTranscriptionOut, responses=ERROR_RESPONSES
)
async def transcribe_in_browser(
    request: Request,
    response: Response,
    auth: AuthDep,
    db: DbDep,
    voice: VoiceDep,
    project_id: Annotated[uuid.UUID, Query()],
    language: LanguageQuery,
) -> BrowserTranscriptionOut:
    """Transcribe from the signed-in Voice page. The body is the audio file."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    audio, content_type = await read_audio(request)
    outcome = await voice.transcribe(
        VoiceCaller(project_id=project_id, user_id=auth.user.id, endpoint=BROWSER_ENDPOINT),
        audio=audio,
        declared_mime=content_type,
        language=language,
        request_id=_request_id(),
    )
    response.headers.update(outcome.headers)
    return BrowserTranscriptionOut(
        request_id=outcome.request_id,
        language=outcome.language,
        model=outcome.model,
        text=outcome.text,
        audio=_audio(outcome),
        latency_ms=outcome.latency_ms,
        attribution=outcome.attribution,
        steps=[VoiceStepOut(**step) for step in outcome.steps],
    )


@router.get("/voice/status", response_model=VoiceStatusOut, responses=ERROR_RESPONSES)
async def voice_status(request: Request, auth: AuthDep) -> VoiceStatusOut:
    """Whether a speech worker is connected, the limits, and the official model for each language."""
    settings = request.app.state.settings
    runtime: VoiceRuntime = request.app.state.voice
    if not runtime.configured:
        state, message = (
            "not_configured",
            "No speech recognition worker is connected to this deployment yet.",
        )
    else:
        await runtime.get_provider()
        if runtime.error is not None:
            state, message = (
                "misconfigured",
                f"The speech recognition worker address cannot be used ({runtime.error}).",
            )
        else:
            state, message = (
                "ready",
                "A speech recognition worker is configured. It is contacted when you transcribe.",
            )
    return VoiceStatusOut(
        state=state,
        configured=runtime.configured,
        message=message,
        limits=VoiceLimitsOut(
            max_upload_bytes=settings.voice_max_upload_bytes,
            max_seconds=settings.voice_max_seconds,
            formats=[item.value for item in AudioFormat],
        ),
        languages=[
            VoiceLanguageOut(
                code=code,
                model=facts.repo_id,
                model_name=facts.card_model_name,
                architecture=facts.architecture,
                parameters=facts.parameters,
                attribution=facts.required_attribution,
                source=facts.source,
            )
            for code, facts in sorted(ASR_MODELS.items())
        ],
    )


@router.get(
    "/projects/{project_id}/voice/requests",
    response_model=VoiceRequestListOut,
    responses=ERROR_RESPONSES,
)
async def recent_voice_requests(
    project_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> VoiceRequestListOut:
    """The most recent transcription requests of a project. Metadata only, never the content."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    rows = (
        (
            await db.execute(
                select(VoiceRequest)
                .where(VoiceRequest.project_id == project_id)
                .order_by(VoiceRequest.created_at.desc(), VoiceRequest.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return VoiceRequestListOut(
        data=[
            VoiceRequestOut(
                id=row.id,
                request_id=row.request_id,
                created_at=row.created_at,
                via="api_key" if row.api_key_id is not None else "browser",
                language=row.language,
                asr_model=row.asr_model,
                audio_format=row.audio_mime,
                audio_bytes=row.audio_bytes,
                audio_duration_ms=row.audio_duration_ms,
                status=row.status,
                validation_ms=row.validation_ms,
                asr_ms=row.asr_ms,
                transcript_chars=row.transcript_chars,
                error_code=row.error_code,
            )
            for row in rows
        ]
    )
