"""API key lifecycle: create, list, revoke, rotate and authenticate.

Keys are never stored. Only an HMAC-SHA-256 of the full key, keyed with a server-side pepper,
is persisted, together with a short public prefix used for identification.
"""

import hmac
import uuid
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
    UnprocessableEntity,
)
from app.core.scopes import DEFAULT_SCOPES, KNOWN_SCOPES
from app.db.base import utcnow
from app.db.models.api_key import ApiKey
from app.db.models.project import Project
from app.integrations.contracts import GatewayPrincipal
from app.security.tokens import (
    HASH_ALGORITHM,
    generate_api_key,
    hash_api_key,
    parse_api_key,
    verify_api_key,
)
from app.services.audit import record_audit
from app.services.auth_service import ClientInfo

MAX_ACTIVE_KEYS_PER_PROJECT = 20
LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)
KeyStatus = Literal["active", "revoked", "expired"]

_BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="natlas-forge"'}


def key_status(key: ApiKey, now: datetime) -> KeyStatus:
    if key.revoked_at is not None:
        return "revoked"
    if key.expires_at is not None and key.expires_at <= now:
        return "expired"
    return "active"


def _pepper(settings: Settings) -> bytes:
    return settings.api_key_pepper.get_secret_value().encode("utf-8")


def validate_scopes(scopes: list[str] | None) -> list[str]:
    if scopes is None:
        return list(DEFAULT_SCOPES)
    unique: list[str] = []
    for scope in scopes:
        if scope not in KNOWN_SCOPES:
            raise UnprocessableEntity(
                f"Unknown scope '{scope}'.",
                code="invalid_scope",
                details=[{"field": "scopes", "message": f"Allowed scopes: {sorted(KNOWN_SCOPES)}"}],
            )
        if scope not in unique:
            unique.append(scope)
    if not unique:
        raise UnprocessableEntity("At least one scope is required.", code="invalid_scope")
    return unique


async def _count_active(db: AsyncSession, project_id: uuid.UUID) -> int:
    now = utcnow()
    return int(
        (
            await db.execute(
                select(func.count(ApiKey.id)).where(
                    ApiKey.project_id == project_id,
                    ApiKey.revoked_at.is_(None),
                    or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
                )
            )
        ).scalar_one()
    )


async def _insert_key(
    db: AsyncSession,
    settings: Settings,
    *,
    project_id: uuid.UUID,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
    created_by: uuid.UUID | None,
    rotated_from_id: uuid.UUID | None,
) -> tuple[ApiKey, str]:
    """Insert a key inside a savepoint so a prefix collision can be retried safely."""
    pepper = _pepper(settings)
    for _ in range(5):
        generated = generate_api_key()
        key = ApiKey(
            project_id=project_id,
            created_by=created_by,
            name=name,
            prefix=generated.prefix,
            key_hash=hash_api_key(generated.full_key, pepper),
            hash_algorithm=HASH_ALGORITHM,
            scopes=list(scopes),
            expires_at=expires_at,
            rotated_from_id=rotated_from_id,
        )
        try:
            async with db.begin_nested():
                db.add(key)
                await db.flush()
        except IntegrityError:
            continue
        return key, generated.full_key
    raise ServiceUnavailable(
        "Could not allocate a unique API key. Try again.", code="key_generation_failed"
    )


async def create_key(
    db: AsyncSession,
    settings: Settings,
    project: Project,
    *,
    actor_id: uuid.UUID,
    name: str,
    scopes: list[str] | None,
    expires_in_days: int | None,
    client: ClientInfo,
) -> tuple[ApiKey, str]:
    if await _count_active(db, project.id) >= MAX_ACTIVE_KEYS_PER_PROJECT:
        raise Conflict(
            f"A project can have at most {MAX_ACTIVE_KEYS_PER_PROJECT} active API keys. "
            "Revoke one first.",
            code="api_key_limit_reached",
        )
    validated = validate_scopes(scopes)
    expires_at = utcnow() + timedelta(days=expires_in_days) if expires_in_days else None
    key, full_key = await _insert_key(
        db,
        settings,
        project_id=project.id,
        name=name,
        scopes=validated,
        expires_at=expires_at,
        created_by=actor_id,
        rotated_from_id=None,
    )
    record_audit(
        db,
        action="api_key.create",
        actor_user_id=actor_id,
        project_id=project.id,
        target_type="api_key",
        target_id=str(key.id),
        details={"prefix": key.prefix, "scopes": validated},
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return key, full_key


async def list_keys(db: AsyncSession, project_id: uuid.UUID) -> list[ApiKey]:
    statement = (
        select(ApiKey)
        .where(ApiKey.project_id == project_id)
        .order_by(ApiKey.created_at.desc())
        .limit(200)
    )
    return list((await db.execute(statement)).scalars().all())


async def _get_key(db: AsyncSession, project_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.project_id == project_id))
    ).scalar_one_or_none()
    if key is None:
        raise NotFound("API key not found.", code="api_key_not_found")
    return key


async def revoke_key(
    db: AsyncSession, project: Project, key_id: uuid.UUID, *, actor_id: uuid.UUID, client: ClientInfo
) -> ApiKey:
    key = await _get_key(db, project.id, key_id)
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        key.revoked_reason = "revoked"
        record_audit(
            db,
            action="api_key.revoke",
            actor_user_id=actor_id,
            project_id=project.id,
            target_type="api_key",
            target_id=str(key.id),
            details={"prefix": key.prefix},
            ip_address=client.ip,
            user_agent=client.user_agent,
            request_id=client.request_id,
        )
        await db.commit()
    return key


async def rotate_key(
    db: AsyncSession,
    settings: Settings,
    project: Project,
    key_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    grace_period_seconds: int,
    client: ClientInfo,
) -> tuple[ApiKey, str, ApiKey]:
    """Create a replacement key and retire the old one, optionally after a grace period."""
    old = await _get_key(db, project.id, key_id)
    now = utcnow()
    if key_status(old, now) != "active":
        raise Conflict("Only an active API key can be rotated.", code="api_key_not_active")

    # Capture values first. The insert runs in a savepoint and never touches `old`.
    name, scopes, expires_at = old.name, list(old.scopes), old.expires_at
    new_key, full_key = await _insert_key(
        db,
        settings,
        project_id=project.id,
        name=name,
        scopes=scopes,
        expires_at=expires_at,
        created_by=actor_id,
        rotated_from_id=old.id,
    )
    if grace_period_seconds > 0:
        grace_end = now + timedelta(seconds=grace_period_seconds)
        if old.expires_at is None or old.expires_at > grace_end:
            old.expires_at = grace_end
    else:
        old.revoked_at = now
        old.revoked_reason = "rotated"
    record_audit(
        db,
        action="api_key.rotate",
        actor_user_id=actor_id,
        project_id=project.id,
        target_type="api_key",
        target_id=str(old.id),
        details={
            "old_prefix": old.prefix,
            "new_prefix": new_key.prefix,
            "grace_period_seconds": grace_period_seconds,
        },
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return new_key, full_key, old


async def authenticate_api_key(
    db: AsyncSession, settings: Settings, credential: str
) -> GatewayPrincipal:
    """Verify an API key and return the caller. Raises 401/403 with safe messages."""
    prefix = parse_api_key(credential)
    if prefix is None:
        raise Unauthorized(
            "The API key is not valid.", code="invalid_api_key", headers=_BEARER_CHALLENGE
        )
    pepper = _pepper(settings)
    row = (
        await db.execute(
            select(ApiKey, Project)
            .join(Project, Project.id == ApiKey.project_id)
            .where(ApiKey.prefix == prefix)
        )
    ).first()
    if row is None:
        # Spend comparable time so unknown prefixes are not distinguishable by timing.
        hmac.compare_digest(hash_api_key(credential, pepper), "0" * 64)
        raise Unauthorized(
            "The API key is not valid.", code="invalid_api_key", headers=_BEARER_CHALLENGE
        )
    key, project = row
    if not verify_api_key(credential, key.key_hash, pepper):
        raise Unauthorized(
            "The API key is not valid.", code="invalid_api_key", headers=_BEARER_CHALLENGE
        )

    now = utcnow()
    if key.revoked_at is not None:
        raise Unauthorized(
            "This API key has been revoked.", code="api_key_revoked", headers=_BEARER_CHALLENGE
        )
    if key.expires_at is not None and key.expires_at <= now:
        raise Unauthorized(
            "This API key has expired.", code="api_key_expired", headers=_BEARER_CHALLENGE
        )
    if project.archived_at is not None:
        raise Forbidden("The project for this API key is archived.", code="project_archived")

    if key.last_used_at is None or now - key.last_used_at > LAST_USED_WRITE_INTERVAL:
        key.last_used_at = now
        await db.commit()
    return GatewayPrincipal(
        project_id=project.id,
        scopes=frozenset(key.scopes),
        source="api_key",
        api_key_id=key.id,
        api_key_prefix=key.prefix,
    )
