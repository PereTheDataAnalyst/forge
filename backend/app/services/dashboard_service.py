"""Dashboard aggregates. Every figure is computed from real stored data."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models.api_key import ApiKey
from app.db.models.project import Project
from app.db.models.telemetry import RequestLog
from app.integrations.contracts import IntegrationRegistry
from app.providers.base import ProviderHealth

RECENT_REQUESTS = 10
RECENT_KEYS = 5
RECENT_EVALUATION_RUNS = 5


async def build_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int,
    provider_health: ProviderHealth,
    integrations: IntegrationRegistry,
    include_provider_detail: bool,
) -> dict[str, Any]:
    now = utcnow()
    since = now - timedelta(days=days)
    owned_projects = select(Project.id).where(
        Project.owner_id == user_id, Project.archived_at.is_(None)
    )

    project_count = (
        await db.execute(
            select(func.count(Project.id)).where(
                Project.owner_id == user_id, Project.archived_at.is_(None)
            )
        )
    ).scalar_one()

    active_key_filter = (
        ApiKey.project_id.in_(owned_projects),
        ApiKey.revoked_at.is_(None),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
    )
    active_keys = (
        await db.execute(select(func.count(ApiKey.id)).where(*active_key_filter))
    ).scalar_one()

    totals = (
        await db.execute(
            select(
                func.count(RequestLog.id),
                func.count(RequestLog.id).filter(RequestLog.outcome == "success"),
                func.count(RequestLog.id).filter(RequestLog.outcome != "success"),
                func.avg(RequestLog.latency_ms).filter(RequestLog.outcome == "success"),
            ).where(RequestLog.project_id.in_(owned_projects), RequestLog.created_at >= since)
        )
    ).one()
    average_latency = totals[3]

    recent_requests = (
        await db.execute(
            select(RequestLog, Project.name)
            .join(Project, Project.id == RequestLog.project_id)
            .where(RequestLog.project_id.in_(owned_projects))
            .order_by(RequestLog.created_at.desc())
            .limit(RECENT_REQUESTS)
        )
    ).all()

    recent_keys = (
        await db.execute(
            select(ApiKey, Project.name)
            .join(Project, Project.id == ApiKey.project_id)
            .where(*active_key_filter)
            .order_by(ApiKey.created_at.desc())
            .limit(RECENT_KEYS)
        )
    ).all()

    evaluation_runs: list[dict[str, Any]] = []
    if integrations.recent_evaluation_runs is not None:
        evaluation_runs = await integrations.recent_evaluation_runs(
            db, user_id, RECENT_EVALUATION_RUNS
        )

    return {
        "window_days": days,
        "totals": {
            "projects": int(project_count),
            "active_api_keys": int(active_keys),
            "requests": int(totals[0]),
            "successful": int(totals[1]),
            "failed": int(totals[2]),
            "average_latency_ms": round(float(average_latency), 1)
            if average_latency is not None
            else None,
        },
        "provider": {
            "status": provider_health.status,
            "model_id": provider_health.model_id,
            "detail": provider_health.detail if include_provider_detail else None,
        },
        "recent_requests": [
            {
                "id": row.id,
                "request_id": row.request_id,
                "created_at": row.created_at,
                "project_id": row.project_id,
                "project_name": project_name,
                "endpoint": row.endpoint,
                "method": row.method,
                "status_code": row.status_code,
                "outcome": row.outcome,
                "error_code": row.error_code,
                "latency_ms": row.latency_ms,
                "model": row.model,
                "language": row.language,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
            }
            for row, project_name in recent_requests
        ],
        "recent_api_keys": [
            {
                "id": key.id,
                "project_id": key.project_id,
                "project_name": project_name,
                "name": key.name,
                "prefix": key.prefix,
                "scopes": list(key.scopes),
                "created_at": key.created_at,
                "last_used_at": key.last_used_at,
                "expires_at": key.expires_at,
            }
            for key, project_name in recent_keys
        ],
        "recent_evaluation_runs": evaluation_runs,
    }
