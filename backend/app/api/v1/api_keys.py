"""Project-level API key management for signed-in users."""

import uuid
from datetime import datetime

from fastapi import APIRouter

from app.api.deps import AuthDep, ClientDep, DbDep, SettingsDep
from app.db.base import utcnow
from app.db.models.api_key import ApiKey
from app.schemas.api_keys import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyList,
    ApiKeyOut,
    ApiKeyRotated,
    ApiKeyRotateRequest,
)
from app.schemas.common import ERROR_RESPONSES
from app.services import api_key_service, project_service

router = APIRouter(prefix="/projects/{project_id}/api-keys", tags=["api-keys"])


def _out(key: ApiKey, now: datetime) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        project_id=key.project_id,
        name=key.name,
        prefix=key.prefix,
        scopes=list(key.scopes),
        status=api_key_service.key_status(key, now),
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        revoked_at=key.revoked_at,
    )


def _created(key: ApiKey, full_key: str, now: datetime) -> ApiKeyCreated:
    return ApiKeyCreated(**_out(key, now).model_dump(), key=full_key)


@router.get("", response_model=ApiKeyList, responses=ERROR_RESPONSES)
async def list_api_keys(project_id: uuid.UUID, auth: AuthDep, db: DbDep) -> ApiKeyList:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    keys = await api_key_service.list_keys(db, project.id)
    now = utcnow()
    return ApiKeyList(data=[_out(key, now) for key in keys])


@router.post("", response_model=ApiKeyCreated, status_code=201, responses=ERROR_RESPONSES)
async def create_api_key(
    project_id: uuid.UUID,
    payload: ApiKeyCreate,
    auth: AuthDep,
    db: DbDep,
    settings: SettingsDep,
    client: ClientDep,
) -> ApiKeyCreated:
    """Create a key. The full key is returned only in this response."""
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    key, full_key = await api_key_service.create_key(
        db,
        settings,
        project,
        actor_id=auth.user.id,
        name=payload.name,
        scopes=payload.scopes,
        expires_in_days=payload.expires_in_days,
        client=client,
    )
    return _created(key, full_key, utcnow())


@router.post("/{key_id}/revoke", response_model=ApiKeyOut, responses=ERROR_RESPONSES)
async def revoke_api_key(
    project_id: uuid.UUID, key_id: uuid.UUID, auth: AuthDep, db: DbDep, client: ClientDep
) -> ApiKeyOut:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    key = await api_key_service.revoke_key(
        db, project, key_id, actor_id=auth.user.id, client=client
    )
    return _out(key, utcnow())


@router.post("/{key_id}/rotate", response_model=ApiKeyRotated, responses=ERROR_RESPONSES)
async def rotate_api_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    settings: SettingsDep,
    client: ClientDep,
    payload: ApiKeyRotateRequest | None = None,
) -> ApiKeyRotated:
    """Issue a replacement key and retire the old one, immediately or after a grace period."""
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    grace = payload.grace_period_seconds if payload is not None else 0
    new_key, full_key, previous = await api_key_service.rotate_key(
        db,
        settings,
        project,
        key_id,
        actor_id=auth.user.id,
        grace_period_seconds=grace,
        client=client,
    )
    now = utcnow()
    return ApiKeyRotated(new_key=_created(new_key, full_key, now), previous=_out(previous, now))
