"""Runtime wiring: the run manager, its logging, startup and shutdown, and the dashboard hook."""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from forge_integrations.config import EvaluationSettings
from forge_integrations.evaluation.runner import EvaluationRunner
from forge_integrations.evaluation.service import RunManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.integration import Evaluation, EvaluationRun
from app.db.models.project import Project
from app.modules.evaluation.gateway_adapter import PlatformModelGateway
from app.modules.evaluation.store import DatabaseRunStore

log = get_logger(__name__)


@dataclass(frozen=True)
class EvaluationRuntime:
    manager: RunManager


class LogObserver:
    """Writes the library's events to the platform log. Events carry ids, counts and error
    codes, never prompts or model output. Keys get a prefix so they cannot clash with the
    standard log record fields."""

    def emit(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("event", "evaluation.event"))
        fields = {f"eval_{key}": value for key, value in event.items() if key != "event"}
        level = logging.DEBUG if name == "evaluation.attempt" else logging.INFO
        log.log(level, name, extra=fields)


async def recent_evaluation_runs(
    session: AsyncSession, user_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    """The dashboard's recent runs: every run of every active project the user owns."""
    found = await session.execute(
        select(EvaluationRun, Evaluation.name)
        .join(Evaluation, Evaluation.id == EvaluationRun.evaluation_id)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(Project.owner_id == user_id, Project.archived_at.is_(None))
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": run.id.hex,
            "project_id": str(run.project_id),
            "evaluation_name": name,
            "status": run.status,
            "model": run.model,
            "created_at": run.created_at.isoformat(),
        }
        for run, name in found.all()
    ]


async def _startup(app: FastAPI) -> None:
    settings = app.state.settings
    observer = LogObserver()
    runner = EvaluationRunner(
        # Looked up per call, so a replaced `app.state.gateway` is honoured.
        PlatformModelGateway(lambda: app.state.gateway),
        observer=observer,
    )
    manager = RunManager(
        runner,
        DatabaseRunStore(app.state.session_factory),
        EvaluationSettings(
            max_cases=settings.eval_max_cases,
            max_concurrency=settings.eval_max_concurrency,
            call_timeout_seconds=settings.eval_call_timeout_seconds,
            max_concurrent_runs_per_project=settings.eval_max_concurrent_runs_per_project,
        ),
        observer=observer,
        max_run_seconds=float(settings.eval_max_run_seconds),
    )
    app.state.evaluation = EvaluationRuntime(manager=manager)


async def _shutdown(app: FastAPI) -> None:
    runtime = getattr(app.state, "evaluation", None)
    if runtime is not None:
        await runtime.manager.shutdown()


def register(app: FastAPI) -> None:
    """Called once by the extension loader, before the application starts."""
    app.state.startup_hooks.append(_startup)
    app.state.shutdown_hooks.append(_shutdown)
    app.state.integrations.recent_evaluation_runs = recent_evaluation_runs
