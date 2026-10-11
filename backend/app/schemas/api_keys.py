import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

KeyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: KeyName
    scopes: list[str] | None = Field(default=None, max_length=20)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    status: Literal["active", "revoked", "expired"]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    """Returned once, at creation. The full key cannot be retrieved again."""

    key: str


class ApiKeyRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grace_period_seconds: int = Field(default=0, ge=0, le=86400)


class ApiKeyRotated(BaseModel):
    new_key: ApiKeyCreated
    previous: ApiKeyOut


class ApiKeyList(BaseModel):
    data: list[ApiKeyOut]
