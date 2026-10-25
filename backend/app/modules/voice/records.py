"""Writes the metadata of a transcription request to the database."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.integration import VoiceRequest
from app.db.models.telemetry import RequestLog, UsageDaily
from app.modules.voice.service import VoiceRecord

log = get_logger("forge.voice")


class DbVoiceRecorder:
    """Writes the request log, the voice record and the usage counters in one transaction.

    A failure is logged and swallowed. Telemetry never fails the caller.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(self, record: VoiceRecord) -> None:
        now = utcnow()
        succeeded = record.outcome == "success"
        try:
            async with self._session_factory() as session:
                session.add(
                    RequestLog(
                        request_id=record.request_id,
                        project_id=record.project_id,
                        api_key_id=record.api_key_id,
                        user_id=record.user_id,
                        source="voice",
                        endpoint=record.endpoint[:120],
                        method="POST",
                        status_code=record.status_code,
                        outcome=record.outcome,
                        error_code=record.error_code,
                        latency_ms=record.latency_ms,
                        provider_latency_ms=record.asr_ms,
                        model=record.model,
                        language=record.language,
                        prompt_tokens=None,
                        completion_tokens=None,
                        stream=False,
                    )
                )
                session.add(
                    VoiceRequest(
                        request_id=record.request_id,
                        project_id=record.project_id,
                        api_key_id=record.api_key_id,
                        user_id=record.user_id,
                        language=record.language,
                        asr_model=record.model,
                        audio_mime=record.audio_format,
                        audio_bytes=record.audio_bytes,
                        audio_duration_ms=record.audio_duration_ms,
                        status="ok" if succeeded else "error",
                        validation_ms=record.validation_ms,
                        asr_ms=record.asr_ms,
                        llm_ms=None,
                        transcript_chars=record.transcript_chars,
                        error_code=record.error_code,
                    )
                )
                insert_statement = pg_insert(UsageDaily).values(
                    project_id=record.project_id,
                    day=now.date(),
                    model=record.model,
                    request_count=1,
                    success_count=1 if succeeded else 0,
                    error_count=0 if succeeded else 1,
                    prompt_tokens=0,
                    completion_tokens=0,
                    updated_at=now,
                )
                excluded = insert_statement.excluded
                await session.execute(
                    insert_statement.on_conflict_do_update(
                        index_elements=[UsageDaily.project_id, UsageDaily.day, UsageDaily.model],
                        set_={
                            "request_count": UsageDaily.request_count + excluded.request_count,
                            "success_count": UsageDaily.success_count + excluded.success_count,
                            "error_count": UsageDaily.error_count + excluded.error_count,
                            "prompt_tokens": UsageDaily.prompt_tokens + excluded.prompt_tokens,
                            "completion_tokens": (
                                UsageDaily.completion_tokens + excluded.completion_tokens
                            ),
                            "updated_at": excluded.updated_at,
                        },
                    )
                )
                await session.commit()
        except Exception:
            log.exception("telemetry.write_failed")
