"""Liveness and readiness probes. Unauthenticated and free of sensitive detail."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app import __version__
from app.core.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)


@router.get("/live")
async def live() -> dict[str, str]:
    """The process is running."""
    return {"status": "ok", "version": __version__}


async def _database_ok(request: Request) -> bool:
    try:
        async with request.app.state.session_factory() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2.0)
        return True
    except Exception as exc:
        log.warning("health.database_unavailable", extra={"error_type": type(exc).__name__})
        return False


async def _redis_ok(request: Request) -> bool:
    try:
        await asyncio.wait_for(request.app.state.redis.ping(), timeout=2.0)
        return True
    except Exception as exc:
        log.warning("health.redis_unavailable", extra={"error_type": type(exc).__name__})
        return False


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Dependencies are reachable. The N-ATLAS provider is reported but never fails readiness."""
    settings = request.app.state.settings
    database_ok, redis_ok = await asyncio.gather(_database_ok(request), _redis_ok(request))
    provider = await request.app.state.provider.health()

    provider_check: dict[str, Any] = {"status": provider.status, "model_id": provider.model_id}
    if not settings.is_production:
        provider_check["detail"] = provider.detail

    core_ok = database_ok and redis_ok
    if not core_ok:
        status = "unavailable"
        response.status_code = 503
    elif provider.status != "ok":
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "checks": {
            "database": {"status": "ok" if database_ok else "unavailable"},
            "redis": {"status": "ok" if redis_ok else "unavailable"},
            "provider": provider_check,
        },
    }
