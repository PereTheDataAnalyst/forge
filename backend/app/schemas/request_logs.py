"""Response shapes for the request log viewer. Everything here comes from stored request rows."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class RequestLogOut(BaseModel):
    id: uuid.UUID
    request_id: str
    created_at: datetime
    source: str
    endpoint: str
    method: str
    status_code: int
    outcome: str
    error_code: str | None
    latency_ms: int | None
    provider_latency_ms: int | None
    model: str | None
    language: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    api_key_id: uuid.UUID | None
    api_key_prefix: str | None
    api_key_name: str | None


class RequestLogPage(BaseModel):
    data: list[RequestLogOut]
    next_cursor: str | None


class LatencyStats(BaseModel):
    """Latency of successful requests only. Failed requests are counted, not timed."""

    avg_ms: int | None
    p50_ms: int | None
    p95_ms: int | None


class MetricsTotals(BaseModel):
    requests: int
    successful: int
    failed: int
    success_rate: float | None
    latency: LatencyStats
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class SeriesPoint(BaseModel):
    start: datetime
    requests: int
    successful: int
    failed: int
    avg_latency_ms: int | None
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    prompt_tokens: int
    completion_tokens: int


class BreakdownItem(BaseModel):
    key: str
    requests: int
    failed: int


class Breakdowns(BaseModel):
    by_source: list[BreakdownItem]
    by_model: list[BreakdownItem]
    by_language: list[BreakdownItem]
    by_error_code: list[BreakdownItem]
    by_endpoint: list[BreakdownItem]
    by_status: list[BreakdownItem]


class RequestMetrics(BaseModel):
    range: str
    since: datetime
    until: datetime
    bucket_seconds: int
    totals: MetricsTotals
    series: list[SeriesPoint]
    breakdowns: Breakdowns


class RequestFacets(BaseModel):
    """Values that exist in the window, for the filter lists. They ignore the other filters."""

    models: list[str]
    error_codes: list[str]
    endpoints: list[str]
