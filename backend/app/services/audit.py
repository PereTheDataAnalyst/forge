"""Audit log helper. Callers commit, so an audit entry is atomic with the change it records."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redaction import redact_mapping
from app.db.models.telemetry import AuditLog


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            details=redact_mapping(details or {}),
            ip_address=ip_address,
            user_agent=(user_agent or "")[:256] or None,
            request_id=request_id,
        )
    )
