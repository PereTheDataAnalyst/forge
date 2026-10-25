"""Queries behind the request log viewer: a filtered, paged list and chart-ready metrics.

Every figure is computed from rows in the `requests` table. Requests that the rate limiter
rejected before they reached the gateway are never written, so they do not appear here.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, extract, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.api_key import ApiKey
from app.db.models.telemetry import RequestLog
from app.services import log_math

FACET_LIMIT = 50
BREAKDOWN_LIMIT = 10


@dataclass(frozen=True, slots=True)
class LogFilters:
    since: datetime
    until: datetime
    step: int
    window: str
    outcome: str | None = None
    source: str | None = None
    language: str | None = None
    model: str | None = None
    error_code: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    api_key_id: uuid.UUID | None = None
    request_id: str | None = None
    min_latency_ms: int | None = None


def _conditions(project_id: uuid.UUID, filters: LogFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        RequestLog.project_id == project_id,
        RequestLog.created_at >= filters.since,
        RequestLog.created_at <= filters.until,
    ]
    if filters.outcome:
        conditions.append(RequestLog.outcome == filters.outcome)
    if filters.source:
        conditions.append(RequestLog.source == filters.source)
    if filters.language:
        conditions.append(RequestLog.language == filters.language)
    if filters.model:
        conditions.append(RequestLog.model == filters.model)
    if filters.error_code:
        conditions.append(RequestLog.error_code == filters.error_code)
    if filters.endpoint:
        conditions.append(RequestLog.endpoint == filters.endpoint)
    if filters.status_code is not None:
        conditions.append(RequestLog.status_code == filters.status_code)
    if filters.api_key_id is not None:
        conditions.append(RequestLog.api_key_id == filters.api_key_id)
    if filters.request_id:
        conditions.append(RequestLog.request_id.startswith(filters.request_id, autoescape=True))
    if filters.min_latency_ms is not None:
        conditions.append(RequestLog.latency_ms >= filters.min_latency_ms)
    return conditions


async def list_requests(
    db: AsyncSession,
    project_id: uuid.UUID,
    filters: LogFilters,
    *,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of requests, newest first. Returns the rows and the cursor for the next page."""
    conditions = _conditions(project_id, filters)
    if cursor is not None:
        after_at, after_id = cursor
        conditions.append(
            or_(
                RequestLog.created_at < after_at,
                and_(RequestLog.created_at == after_at, RequestLog.id < after_id),
            )
        )
    statement = (
        select(RequestLog, ApiKey.prefix, ApiKey.name)
        .outerjoin(ApiKey, ApiKey.id == RequestLog.api_key_id)
        .where(*conditions)
        .order_by(RequestLog.created_at.desc(), RequestLog.id.desc())
        .limit(limit + 1)
    )
    rows = (await db.execute(statement)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1][0]
        next_cursor = log_math.encode_cursor(last.created_at, last.id)
    items = [
        {
            "id": row.id,
            "request_id": row.request_id,
            "created_at": row.created_at,
            "source": row.source,
            "endpoint": row.endpoint,
            "method": row.method,
            "status_code": row.status_code,
            "outcome": row.outcome,
            "error_code": row.error_code,
            "latency_ms": row.latency_ms,
            "provider_latency_ms": row.provider_latency_ms,
            "model": row.model,
            "language": row.language,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "api_key_id": row.api_key_id,
            "api_key_prefix": key_prefix,
            "api_key_name": key_name,
        }
        for row, key_prefix, key_name in page
    ]
    return items, next_cursor


def _latency_columns() -> tuple[Any, Any, Any]:
    """Average and percentiles of latency over successful requests.

    percentile_cont ignores NULL input, so a CASE without ELSE restricts it to successes. The
    fractions are written as constants, not bind parameters, so PostgreSQL sees one expression.
    """
    success = RequestLog.outcome == "success"
    timed = case((success, RequestLog.latency_ms))
    return (
        func.avg(RequestLog.latency_ms).filter(success),
        func.percentile_cont(literal_column("0.5")).within_group(timed),
        func.percentile_cont(literal_column("0.95")).within_group(timed),
    )


async def _breakdown(
    db: AsyncSession, conditions: list[ColumnElement[bool]], column: Any, limit: int
) -> list[dict[str, Any]]:
    count = func.count(RequestLog.id)
    statement = (
        select(
            column,
            count,
            func.count(RequestLog.id).filter(RequestLog.outcome != "success"),
        )
        .where(*conditions, column.is_not(None))
        .group_by(column)
        .order_by(count.desc(), column)
        .limit(limit)
    )
    rows = (await db.execute(statement)).all()
    return [
        {"key": str(key), "requests": log_math.to_int(total), "failed": log_math.to_int(failed)}
        for key, total, failed in rows
    ]


async def metrics(
    db: AsyncSession, project_id: uuid.UUID, filters: LogFilters
) -> dict[str, Any]:
    """Totals, a zero-filled time series and breakdowns for the filtered window."""
    conditions = _conditions(project_id, filters)
    success = RequestLog.outcome == "success"
    avg_latency, p50, p95 = _latency_columns()
    tokens_in = func.coalesce(func.sum(RequestLog.prompt_tokens), 0)
    tokens_out = func.coalesce(func.sum(RequestLog.completion_tokens), 0)

    total_row = (
        await db.execute(
            select(
                func.count(RequestLog.id),
                func.count(RequestLog.id).filter(success),
                avg_latency,
                p50,
                p95,
                tokens_in,
                tokens_out,
            ).where(*conditions)
        )
    ).one()
    requests = log_math.to_int(total_row[0])
    successful = log_math.to_int(total_row[1])
    prompt_tokens = log_math.to_int(total_row[5])
    completion_tokens = log_math.to_int(total_row[6])
    totals = {
        "requests": requests,
        "successful": successful,
        "failed": requests - successful,
        "success_rate": log_math.success_rate(requests, successful),
        "latency": {
            "avg_ms": log_math.to_ms(total_row[2]),
            "p50_ms": log_math.to_ms(total_row[3]),
            "p95_ms": log_math.to_ms(total_row[4]),
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

    # The step is an int chosen from a fixed table, written as a constant so the SELECT list and
    # GROUP BY hold the same expression. The label is used in GROUP BY and ORDER BY.
    bucket = func.floor(
        extract("epoch", RequestLog.created_at) / literal_column(str(int(filters.step)))
    ).label("bucket")
    avg_b, p50_b, p95_b = _latency_columns()
    series_rows = (
        await db.execute(
            select(
                bucket,
                func.count(RequestLog.id),
                func.count(RequestLog.id).filter(success),
                avg_b,
                p50_b,
                p95_b,
                func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
                func.coalesce(func.sum(RequestLog.completion_tokens), 0),
            )
            .where(*conditions)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    found: dict[int, dict[str, Any]] = {}
    for row in series_rows:
        found[int(row[0])] = {
            "requests": log_math.to_int(row[1]),
            "successful": log_math.to_int(row[2]),
            "avg_latency_ms": log_math.to_ms(row[3]),
            "p50_latency_ms": log_math.to_ms(row[4]),
            "p95_latency_ms": log_math.to_ms(row[5]),
            "prompt_tokens": log_math.to_int(row[6]),
            "completion_tokens": log_math.to_int(row[7]),
        }
    series = log_math.fill_series(found, filters.since, filters.until, filters.step)

    failed_conditions = [*conditions, RequestLog.outcome != "success"]
    by_error_code = await _breakdown(db, failed_conditions, RequestLog.error_code, BREAKDOWN_LIMIT)
    breakdowns = {
        "by_source": await _breakdown(db, conditions, RequestLog.source, BREAKDOWN_LIMIT),
        "by_model": await _breakdown(db, conditions, RequestLog.model, BREAKDOWN_LIMIT),
        "by_language": await _breakdown(db, conditions, RequestLog.language, BREAKDOWN_LIMIT),
        "by_error_code": by_error_code,
        "by_endpoint": await _breakdown(db, conditions, RequestLog.endpoint, BREAKDOWN_LIMIT),
        "by_status": await _breakdown(db, conditions, RequestLog.status_code, BREAKDOWN_LIMIT),
    }
    return {
        "range": filters.window,
        "since": filters.since,
        "until": filters.until,
        "bucket_seconds": filters.step,
        "totals": totals,
        "series": series,
        "breakdowns": breakdowns,
    }


async def facets(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, until: datetime
) -> dict[str, list[str]]:
    """Distinct models, error codes and endpoints in the window, most used first."""
    base = [
        RequestLog.project_id == project_id,
        RequestLog.created_at >= since,
        RequestLog.created_at <= until,
    ]
    result: dict[str, list[str]] = {}
    for name, column in (
        ("models", RequestLog.model),
        ("error_codes", RequestLog.error_code),
        ("endpoints", RequestLog.endpoint),
    ):
        count = func.count(RequestLog.id)
        statement = (
            select(column)
            .where(*base, column.is_not(None))
            .group_by(column)
            .order_by(count.desc(), column)
            .limit(FACET_LIMIT)
        )
        result[name] = [str(value) for value in (await db.execute(statement)).scalars().all()]
    return result
