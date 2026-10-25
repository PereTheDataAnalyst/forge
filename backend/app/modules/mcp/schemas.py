"""Request and response shapes for MCP client management."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpAccessGroupOut(BaseModel):
    scope: str
    label: str
    description: str
    tools: list[str]
    read_only: bool


class McpCatalogOut(BaseModel):
    state: Literal["disabled", "starting", "ready", "unavailable"]
    available: bool
    message: str
    endpoint_path: str
    groups: list[McpAccessGroupOut]
    default_access: list[str]
    expiry_days: list[int]
    default_expiry_days: int
    max_clients_per_project: int


class McpClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    access: list[str] = Field(min_length=1, max_length=12)
    expires_in_days: Literal[30, 90] = 30

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("The name cannot be empty.")
        if any(ord(char) < 32 for char in value):
            raise ValueError("The name cannot contain control characters.")
        return cleaned


class McpClientOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    key_prefix: str | None
    allowed_tools: list[str]
    access: list[str]
    status: Literal["active", "expired", "revoked"]
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class McpClientCreated(McpClientOut):
    key: str


class McpClientListOut(BaseModel):
    data: list[McpClientOut]
