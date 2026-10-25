"""Database access for MCP: key lookup for authentication, and client management."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.api_key import ApiKey
from app.db.models.integration import McpClient
from app.db.models.project import Project
from app.modules.mcp import catalog
from app.modules.mcp.auth import McpCredentials
from app.services import api_key_service
from app.services.audit import record_audit
from app.services.auth_service import ClientInfo

log = get_logger("forge.mcp")


class DbCredentialStore:
    """Looks a presented key up, with its project and MCP client, for the authenticator."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find(self, prefix: str) -> McpCredentials | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ApiKey, Project.archived_at, McpClient)
                    .join(Project, Project.id == ApiKey.project_id)
                    .outerjoin(
                        McpClient,
                        and_(
                            McpClient.api_key_id == ApiKey.id,
                            McpClient.project_id == ApiKey.project_id,
                        ),
                    )
                    .where(ApiKey.prefix == prefix)
                    .order_by(McpClient.created_at.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        key, archived_at, client = row
        return McpCredentials(
            key_id=key.id,
            key_hash=key.key_hash,
            key_scopes=tuple(key.scopes),
            key_expires_at=key.expires_at,
            key_revoked_at=key.revoked_at,
            project_id=key.project_id,
            project_archived=archived_at is not None,
            client_id=client.id if client is not None else None,
            client_name=client.name if client is not None else None,
            client_allowed_tools=tuple(client.allowed_tools) if client is not None else (),
            client_revoked_at=client.revoked_at if client is not None else None,
            client_last_seen_at=client.last_seen_at if client is not None else None,
        )

    async def touch(self, client_id: uuid.UUID, key_id: uuid.UUID, when: datetime) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(McpClient).where(McpClient.id == client_id).values(last_seen_at=when)
            )
            await session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=when))
            await session.commit()

    async def ping(self) -> bool:
        try:
            async with self._session_factory() as session:
                await session.execute(select(1))
            return True
        except Exception:
            return False


def client_status(client: McpClient, key: ApiKey | None, now: datetime) -> str:
    if client.revoked_at is not None or key is None or key.revoked_at is not None:
        return "revoked"
    if key.expires_at is not None and key.expires_at <= now:
        return "expired"
    return "active"


def client_view(client: McpClient, key: ApiKey | None, now: datetime) -> dict[str, Any]:
    return {
        "id": client.id,
        "project_id": client.project_id,
        "name": client.name,
        "key_prefix": key.prefix if key is not None else None,
        "allowed_tools": list(client.allowed_tools),
        "access": catalog.access_for(client.allowed_tools),
        "status": client_status(client, key, now),
        "created_at": client.created_at,
        "last_seen_at": client.last_seen_at,
        "expires_at": key.expires_at if key is not None else None,
        "revoked_at": client.revoked_at or (key.revoked_at if key is not None else None),
    }


async def list_clients(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(McpClient, ApiKey)
            .outerjoin(ApiKey, ApiKey.id == McpClient.api_key_id)
            .where(McpClient.project_id == project_id)
            .order_by(McpClient.created_at.desc(), McpClient.id.desc())
        )
    ).all()
    now = utcnow()
    return [client_view(client, key, now) for client, key in rows]


async def _get_client(
    db: AsyncSession, project_id: uuid.UUID, client_id: uuid.UUID
) -> tuple[McpClient, ApiKey | None]:
    row = (
        await db.execute(
            select(McpClient, ApiKey)
            .outerjoin(ApiKey, ApiKey.id == McpClient.api_key_id)
            .where(McpClient.id == client_id, McpClient.project_id == project_id)
        )
    ).first()
    if row is None:
        raise NotFound("That MCP client does not exist.", code="mcp_client_not_found")
    return row[0], row[1]


async def _count_active(db: AsyncSession, project_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count(McpClient.id)).where(
                    McpClient.project_id == project_id, McpClient.revoked_at.is_(None)
                )
            )
        ).scalar_one()
    )


async def create_client(
    db: AsyncSession,
    settings: Settings,
    project: Project,
    *,
    actor_id: uuid.UUID,
    name: str,
    access: list[str],
    expires_in_days: int,
    client: ClientInfo,
) -> tuple[dict[str, Any], str]:
    """Create an MCP client: a key that only the MCP endpoint accepts, and the client row.

    Returns the client view and the full key. The key is shown once and cannot be read again.
    """
    project_id = project.id
    if await _count_active(db, project_id) >= catalog.MAX_CLIENTS_PER_PROJECT:
        raise Conflict(
            f"A project can have at most {catalog.MAX_CLIENTS_PER_PROJECT} active MCP clients. "
            "Revoke one first.",
            code="mcp_client_limit_reached",
        )
    key, full_key = await api_key_service.create_key(
        db,
        settings,
        project,
        actor_id=actor_id,
        name=f"MCP: {name}"[:80],
        scopes=[catalog.INVOKE_SCOPE],
        expires_in_days=expires_in_days,
        client=client,
    )
    key_id = key.id
    row = McpClient(
        id=uuid.uuid4(),
        project_id=project_id,
        api_key_id=key_id,
        name=name,
        allowed_tools=catalog.tools_for(access),
        created_by=actor_id,
    )
    try:
        db.add(row)
        record_audit(
            db,
            action="mcp_client.create",
            actor_user_id=actor_id,
            project_id=project_id,
            target_type="mcp_client",
            target_id=str(row.id),
            details={"name": name, "access": access, "prefix": key.prefix},
            ip_address=client.ip,
            user_agent=client.user_agent,
            request_id=client.request_id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        # Never leave a usable key behind without its client row. A rollback expires loaded
        # objects, so the project is read again instead of touching the old instance.
        try:
            fresh = await db.get(Project, project_id)
            if fresh is not None:
                await api_key_service.revoke_key(
                    db, fresh, key_id, actor_id=actor_id, client=client
                )
        except Exception:
            log.exception("mcp.orphan_key_not_revoked")
        raise
    return client_view(row, key, utcnow()), full_key


async def revoke_client(
    db: AsyncSession,
    project: Project,
    client_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    client: ClientInfo,
) -> dict[str, Any]:
    """Revoke the client and its key. Safe to repeat."""
    row, key = await _get_client(db, project.id, client_id)
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        record_audit(
            db,
            action="mcp_client.revoke",
            actor_user_id=actor_id,
            project_id=project.id,
            target_type="mcp_client",
            target_id=str(row.id),
            details={"name": row.name, "prefix": key.prefix if key is not None else None},
            ip_address=client.ip,
            user_agent=client.user_agent,
            request_id=client.request_id,
        )
        await db.commit()
    if key is not None and key.revoked_at is None:
        await api_key_service.revoke_key(db, project, key.id, actor_id=actor_id, client=client)
    row, key = await _get_client(db, project.id, client_id)
    return client_view(row, key, utcnow())
