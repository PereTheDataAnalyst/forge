"""Application factory.

Run with:  uvicorn app.main:create_app --factory
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import __version__
from app.api.v1 import api_router
from app.cache.rate_limit import RateLimiter
from app.cache.redis import create_redis
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.integrations.contracts import IntegrationRegistry
from app.integrations.loader import load_extension_modules
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.body_limit import BodySizeLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.providers.natlas import NAtlasProvider
from app.services.gateway_service import GatewayService
from app.web.spa import mount_spa

log = get_logger(__name__)


async def _log_connectivity(app: FastAPI) -> None:
    """Non-fatal startup diagnostics so a misconfigured environment is obvious in the log."""
    try:
        async with app.state.session_factory() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3.0)
        log.info("startup.database_reachable")
    except Exception as exc:
        log.error("startup.database_unreachable", extra={"error_type": type(exc).__name__})
    try:
        await asyncio.wait_for(app.state.redis.ping(), timeout=3.0)
        log.info("startup.redis_reachable")
    except Exception as exc:
        log.error("startup.redis_unreachable", extra={"error_type": type(exc).__name__})
    health = await app.state.provider.health()
    log.info("startup.provider_status", extra={"provider_status": health.status})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info("app.starting", extra=settings.safe_summary())

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    redis = create_redis(settings)
    app.state.redis = redis
    app.state.rate_limiter = RateLimiter(redis, fail_open=settings.rate_limit_fail_open)
    provider = NAtlasProvider(settings)
    await provider.startup()
    app.state.provider = provider
    app.state.gateway = GatewayService(
        settings=settings,
        provider=provider,
        limiter=app.state.rate_limiter,
        session_factory=app.state.session_factory,
    )

    for hook in app.state.startup_hooks:
        await hook(app)
    await _log_connectivity(app)
    try:
        yield
    finally:
        for hook in reversed(app.state.shutdown_hooks):
            try:
                await hook(app)
            except Exception:
                log.exception("shutdown_hook_failed")
        await provider.shutdown()
        await redis.aclose()
        await engine.dispose()
        log.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    docs_enabled = settings.enable_api_docs
    app = FastAPI(
        title="N-ATLAS Forge API",
        version=__version__,
        description="Developer infrastructure for building with N-ATLAS.",
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.body_limit_overrides = {}
    app.state.startup_hooks = []
    app.state.shutdown_hooks = []
    app.state.integrations = IntegrationRegistry()

    register_exception_handlers(app)

    # Middleware is added innermost first. Request flow: RequestId > SecurityHeaders >
    # AccessLog > BodySizeLimit > CORS > application.
    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
            expose_headers=[
                "X-Request-ID",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "Retry-After",
            ],
            max_age=600,
        )
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_request_body_bytes,
        overrides=app.state.body_limit_overrides,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(RequestIdMiddleware, trusted_proxy_count=settings.trusted_proxy_count)

    app.include_router(api_router)
    load_extension_modules(app)
    # Last, so the web app only answers paths that no API route matched.
    mount_spa(app, settings.frontend_dist_dir)
    return app
