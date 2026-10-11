import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DashboardTotals(BaseModel):
    projects: int
    active_api_keys: int
    requests: int
    successful: int
    failed: int
    average_latency_ms: float | None


class ProviderStatusOut(BaseModel):
    status: Literal["ok", "not_configured", "unreachable", "loading", "error"]
    model_id: str | None
    detail: str | None


class RecentRequestOut(BaseModel):
    id: uuid.UUID
    request_id: str
    created_at: datetime
    project_id: uuid.UUID
    project_name: str
    endpoint: str
    method: str
    status_code: int
    outcome: str
    error_code: str | None
    latency_ms: int | None
    model: str | None
    language: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


class RecentApiKeyOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class RecentEvaluationRunOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    evaluation_name: str | None = None
    model: str | None = None
    created_at: str | None = None


class DashboardSummary(BaseModel):
    window_days: int
    totals: DashboardTotals
    provider: ProviderStatusOut
    recent_requests: list[RecentRequestOut]
    recent_api_keys: list[RecentApiKeyOut]
    recent_evaluation_runs: list[RecentEvaluationRunOut]
