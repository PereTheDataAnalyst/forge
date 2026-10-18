"""Database operations for evaluation datasets (the `evaluations` and `evaluation_cases` tables)."""

import uuid

from forge_integrations.evaluation.models import Dataset
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.base import utcnow
from app.db.models.integration import Evaluation, EvaluationCase
from app.modules.evaluation.mapping import dataset_to_case_rows
from app.services.audit import record_audit
from app.services.auth_service import ClientInfo


async def list_evaluations(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[Evaluation, int]]:
    """Active datasets of a project with their case counts, newest first."""
    found = await db.execute(
        select(Evaluation)
        .where(Evaluation.project_id == project_id, Evaluation.archived_at.is_(None))
        .order_by(Evaluation.created_at.desc())
    )
    evaluations = list(found.scalars().all())
    if not evaluations:
        return []
    counted = await db.execute(
        select(EvaluationCase.evaluation_id, func.count(EvaluationCase.id))
        .where(EvaluationCase.evaluation_id.in_([evaluation.id for evaluation in evaluations]))
        .group_by(EvaluationCase.evaluation_id)
    )
    counts = {row[0]: int(row[1]) for row in counted.all()}
    return [(evaluation, counts.get(evaluation.id, 0)) for evaluation in evaluations]


async def get_evaluation(
    db: AsyncSession, project_id: uuid.UUID, evaluation_id: uuid.UUID
) -> Evaluation:
    """An active dataset of this project. A missing, archived or foreign one is a 404."""
    found = await db.execute(
        select(Evaluation).where(
            Evaluation.id == evaluation_id,
            Evaluation.project_id == project_id,
            Evaluation.archived_at.is_(None),
        )
    )
    evaluation = found.scalar_one_or_none()
    if evaluation is None:
        raise NotFound("Evaluation not found.", code="evaluation_not_found")
    return evaluation


async def load_cases(db: AsyncSession, evaluation_id: uuid.UUID) -> list[EvaluationCase]:
    found = await db.execute(
        select(EvaluationCase)
        .where(EvaluationCase.evaluation_id == evaluation_id)
        .order_by(EvaluationCase.position)
    )
    return list(found.scalars().all())


async def create_evaluation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    dataset: Dataset,
    max_per_project: int,
    client: ClientInfo,
) -> tuple[Evaluation, list[EvaluationCase]]:
    active = await db.scalar(
        select(func.count(Evaluation.id)).where(
            Evaluation.project_id == project_id, Evaluation.archived_at.is_(None)
        )
    )
    if int(active or 0) >= max_per_project:
        raise Conflict(
            f"A project can hold {max_per_project} evaluations. Archive one first.",
            code="evaluation_limit_reached",
        )
    evaluation = Evaluation(
        project_id=project_id,
        name=dataset.name,
        description=dataset.description or None,
        created_by=user_id,
    )
    db.add(evaluation)
    await db.flush()
    cases = [
        EvaluationCase(evaluation_id=evaluation.id, **row) for row in dataset_to_case_rows(dataset)
    ]
    db.add_all(cases)
    await db.flush()
    record_audit(
        db,
        action="evaluation.create",
        actor_user_id=user_id,
        project_id=project_id,
        target_type="evaluation",
        target_id=str(evaluation.id),
        details={"cases": len(cases)},
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return evaluation, cases


async def archive_evaluation(
    db: AsyncSession, evaluation: Evaluation, *, user_id: uuid.UUID, client: ClientInfo
) -> None:
    evaluation.archived_at = utcnow()
    record_audit(
        db,
        action="evaluation.archive",
        actor_user_id=user_id,
        project_id=evaluation.project_id,
        target_type="evaluation",
        target_id=str(evaluation.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
