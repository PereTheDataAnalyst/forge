"""PostgreSQL storage for finished evaluation runs. Implements the library's `RunStore`."""

from __future__ import annotations

import uuid
from typing import Any

from forge_integrations.evaluation.models import Run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.integration import EvaluationCase, EvaluationResult, EvaluationRun
from app.modules.evaluation.mapping import rows_to_run, run_row_summary, run_to_rows


class DatabaseRunStore:
    """Every method is scoped to a project: a run of another project is never returned."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, run: Run) -> None:
        run_row, result_rows = run_to_rows(run)
        async with self._session_factory() as session:
            session.add(EvaluationRun(**run_row))
            await session.flush()
            session.add_all([EvaluationResult(**row) for row in result_rows])
            await session.commit()

    async def get(self, project_id: str, run_id: str) -> Run | None:
        try:
            project_uuid = uuid.UUID(project_id)
            run_uuid = uuid.UUID(hex=run_id)
        except ValueError:
            return None
        async with self._session_factory() as session:
            found = await session.execute(
                select(EvaluationRun).where(
                    EvaluationRun.id == run_uuid, EvaluationRun.project_id == project_uuid
                )
            )
            run_row = found.scalar_one_or_none()
            if run_row is None:
                return None
            results = await session.execute(
                select(EvaluationResult)
                .join(EvaluationCase, EvaluationCase.id == EvaluationResult.case_id)
                .where(EvaluationResult.run_id == run_uuid)
                .order_by(EvaluationCase.position)
            )
            return rows_to_run(run_row, results.scalars().all())

    # Defined last: the name shadows the built-in `list` inside this class body.
    async def list(self, project_id: str, *, limit: int) -> list[dict[str, Any]]:
        try:
            project_uuid = uuid.UUID(project_id)
        except ValueError:
            return []
        async with self._session_factory() as session:
            found = await session.execute(
                select(EvaluationRun)
                .where(EvaluationRun.project_id == project_uuid)
                .order_by(EvaluationRun.created_at.desc())
                .limit(limit)
            )
            return [run_row_summary(row) for row in found.scalars().all()]
