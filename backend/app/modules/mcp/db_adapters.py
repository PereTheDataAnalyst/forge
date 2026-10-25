"""MCP ports that read or write the database: usage summary, project info and audit events.

Events hold names, sizes, codes and timings. The library never puts prompts, output, transcripts,
audio or tokens in them, and the platform redaction runs again before the write.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.redaction import redact_mapping
from app.db.base import utcnow
from app.db.models.project import Project
from app.db.models.telemetry import AuditLog, RequestLog


class PlatformUsageReader:
    """The library's `UsageReader` port: aggregate counts from the project's request log."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def summary(self, project_id: str, *, days: int) -> Mapping[str, Any]:
        pid = uuid.UUID(project_id)
        since = utcnow() - timedelta(days=days)
        scope = (RequestLog.project_id == pid, RequestLog.created_at >= since)
        success = RequestLog.outcome == "success"
        async with self._session_factory() as session:
            total = (
                await session.execute(
                    select(
                        func.count(RequestLog.id),
                        func.count(RequestLog.id).filter(success),
                        func.avg(RequestLog.latency_ms).filter(success),
                        func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
                        func.coalesce(func.sum(RequestLog.completion_tokens), 0),
                    ).where(*scope)
                )
            ).one()
            by_model = await self._grouped(session, RequestLog.model, scope)
            by_source = await self._grouped(session, RequestLog.source, scope)
        requests, successful = int(total[0]), int(total[1])
        return {
            "window_days": days,
            "requests": requests,
            "successful": successful,
            "failed": requests - successful,
            "success_rate": round(successful / requests, 4) if requests else None,
            "avg_latency_ms": int(float(total[2]) + 0.5) if total[2] is not None else None,
            "prompt_tokens": int(total[3]),
            "completion_tokens": int(total[4]),
            "by_model": by_model,
            "by_source": by_source,
        }

    @staticmethod
    async def _grouped(session: AsyncSession, column: Any, scope: tuple[Any, ...]) -> list[dict]:
        count = func.count(RequestLog.id)
        rows = (
            await session.execute(
                select(column, count)
                .where(*scope, column.is_not(None))
                .group_by(column)
                .order_by(count.desc(), column)
                .limit(10)
            )
        ).all()
        return [{"name": str(name), "requests": int(total)} for name, total in rows]


class PlatformProjectInfo:
    """The library's `ProjectInfoProvider`: only the name, description and creation date."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, project_id: str) -> Mapping[str, Any] | None:
        async with self._session_factory() as session:
            project = await session.get(Project, uuid.UUID(project_id))
        if project is None:
            return None
        created: datetime = project.created_at
        return {
            "name": project.name,
            "description": project.description,
            "created_at": created.isoformat(),
        }


class DbAuditSink:
    """The library's `AuditSink`, writing each event as a row in `audit_logs`.

    Events hold names, sizes, codes and timings. The library never puts prompts, output,
    transcripts, audio or tokens in them, and the platform redaction runs again before the write.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: Mapping[str, Any]) -> None:
        data = dict(event)
        action = str(data.pop("event", "mcp.event"))[:64]
        project_id = _uuid_or_none(data.get("project_id"))
        client_id = data.get("client_id")
        request_id = data.get("request_id")
        async with self._session_factory() as session:
            session.add(
                AuditLog(
                    action=action,
                    project_id=project_id,
                    target_type="mcp_client" if client_id else None,
                    target_id=str(client_id)[:64] if client_id else None,
                    request_id=str(request_id)[:64] if request_id else None,
                    details=redact_mapping(data),
                )
            )
            await session.commit()


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None
