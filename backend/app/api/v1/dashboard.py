"""Dashboard summary for signed-in users."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AuthDep, DbDep, IntegrationsDep, ProviderDep
from app.schemas.common import ERROR_RESPONSES
from app.schemas.dashboard import DashboardSummary
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary, responses=ERROR_RESPONSES)
async def summary(
    auth: AuthDep,
    db: DbDep,
    provider: ProviderDep,
    integrations: IntegrationsDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> DashboardSummary:
    """Totals and recent activity computed from stored request data across your projects."""
    health = await provider.health()
    data = await dashboard_service.build_summary(
        db,
        auth.user.id,
        days=days,
        provider_health=health,
        integrations=integrations,
        include_provider_detail=True,
    )
    return DashboardSummary.model_validate(data)
