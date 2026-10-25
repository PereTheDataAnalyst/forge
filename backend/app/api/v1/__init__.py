"""Version 1 of the HTTP API, mounted under /api/v1."""

from fastapi import APIRouter

from app.api.v1 import (
    api_keys,
    auth,
    dashboard,
    gateway,
    health,
    playground,
    projects,
    request_logs,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(api_keys.router)
api_router.include_router(gateway.router)
api_router.include_router(dashboard.router)
api_router.include_router(playground.router)
api_router.include_router(request_logs.router)
