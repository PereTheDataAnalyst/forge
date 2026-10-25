"""Request log viewer and metrics for the project owner (cookie session)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthDep, DbDep
from app.core.errors import UnprocessableEntity
from app.db.base import utcnow
from app.schemas.common import ERROR_RESPONSES
from app.schemas.request_logs import (
    RequestFacets,
    RequestLogOut,
    RequestLogPage,
    RequestMetrics,
)
from app.services import log_math, project_service, request_log_service
from app.services.request_log_service import LogFilters

router = APIRouter(prefix="/projects/{project_id}/requests", tags=["request-logs"])

RangeName = Literal["1h", "6h", "24h", "7d", "30d", "90d"]
Outcome = Literal["success", "error"]
Source = Literal["api_key", "playground", "mcp", "evaluation", "voice"]
Language = Literal["en", "ha", "ig", "yo"]


def log_filters(
    window: Annotated[RangeName, Query(alias="range")] = "24h",
    outcome: Annotated[Outcome | None, Query()] = None,
    source: Annotated[Source | None, Query()] = None,
    language: Annotated[Language | None, Query()] = None,
    model: Annotated[str | None, Query(max_length=120)] = None,
    error_code: Annotated[str | None, Query(pattern=r"^[a-z0-9_]{1,64}$")] = None,
    endpoint: Annotated[str | None, Query(max_length=120)] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    api_key_id: Annotated[uuid.UUID | None, Query()] = None,
    request_id: Annotated[str | None, Query(max_length=64)] = None,
    min_latency_ms: Annotated[int | None, Query(ge=0, le=3_600_000)] = None,
) -> LogFilters:
    since, until, step = log_math.window_for(window, utcnow())
    cleaned_request_id = None
    if request_id is not None and request_id.strip():
        try:
            cleaned_request_id = log_math.clean_request_id(request_id)
        except ValueError:
            raise UnprocessableEntity(
                "A request ID has letters, digits, dots, dashes and underscores only.",
                code="invalid_request_id",
            ) from None
    return LogFilters(
        since=since,
        until=until,
        step=step,
        window=window,
        outcome=outcome,
        source=source,
        language=language,
        model=model or None,
        error_code=error_code,
        endpoint=endpoint or None,
        status_code=status_code,
        api_key_id=api_key_id,
        request_id=cleaned_request_id,
        min_latency_ms=min_latency_ms,
    )


FiltersDep = Annotated[LogFilters, Depends(log_filters)]


@router.get("", response_model=RequestLogPage, responses=ERROR_RESPONSES)
async def list_requests(
    project_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    filters: FiltersDep,
    cursor: Annotated[str | None, Query(max_length=log_math.MAX_CURSOR_CHARS)] = None,
    limit: Annotated[int, Query(ge=1, le=log_math.MAX_PAGE_SIZE)] = log_math.DEFAULT_PAGE_SIZE,
) -> RequestLogPage:
    """Requests recorded for the project, newest first, with the filters applied."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    position = None
    if cursor:
        try:
            position = log_math.decode_cursor(cursor)
        except ValueError:
            raise UnprocessableEntity(
                "The cursor is not valid. Start again from the first page.", code="invalid_cursor"
            ) from None
    items, next_cursor = await request_log_service.list_requests(
        db, project_id, filters, cursor=position, limit=limit
    )
    return RequestLogPage(data=[RequestLogOut(**item) for item in items], next_cursor=next_cursor)


@router.get("/metrics", response_model=RequestMetrics, responses=ERROR_RESPONSES)
async def request_metrics(
    project_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    filters: FiltersDep,
) -> RequestMetrics:
    """Totals, a time series and breakdowns for the same window and filters as the list."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    body = await request_log_service.metrics(db, project_id, filters)
    return RequestMetrics(**body)


@router.get("/facets", response_model=RequestFacets, responses=ERROR_RESPONSES)
async def request_facets(
    project_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    window: Annotated[RangeName, Query(alias="range")] = "24h",
) -> RequestFacets:
    """Models, error codes and endpoints seen in the window, for the filter lists."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    since, until, _ = log_math.window_for(window, utcnow())
    return RequestFacets(**await request_log_service.facets(db, project_id, since, until))
