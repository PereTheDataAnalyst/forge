"""Project management. A project is owned by exactly one user."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.base import utcnow
from app.db.models.api_key import ApiKey
from app.db.models.project import Project
from app.services.audit import record_audit
from app.services.auth_service import ClientInfo

MAX_ACTIVE_PROJECTS_PER_USER = 20


async def list_projects(db: AsyncSession, owner_id: uuid.UUID) -> list[tuple[Project, int]]:
    now = utcnow()
    counts = (
        select(ApiKey.project_id.label("project_id"), func.count(ApiKey.id).label("active_keys"))
        .where(ApiKey.revoked_at.is_(None), or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now))
        .group_by(ApiKey.project_id)
        .subquery()
    )
    statement = (
        select(Project, func.coalesce(counts.c.active_keys, 0))
        .outerjoin(counts, counts.c.project_id == Project.id)
        .where(Project.owner_id == owner_id, Project.archived_at.is_(None))
        .order_by(Project.created_at.desc())
    )
    rows = (await db.execute(statement)).all()
    return [(project, int(count)) for project, count in rows]


async def get_owned_project(
    db: AsyncSession, owner_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """Return the project or raise 404. Other users' projects are indistinguishable from missing ones."""
    project = (
        await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.owner_id == owner_id,
                Project.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Project not found.", code="project_not_found")
    return project


async def count_active_keys(db: AsyncSession, project_id: uuid.UUID) -> int:
    now = utcnow()
    return int(
        (
            await db.execute(
                select(func.count(ApiKey.id)).where(
                    ApiKey.project_id == project_id,
                    ApiKey.revoked_at.is_(None),
                    or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
                )
            )
        ).scalar_one()
    )


async def create_project(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    client: ClientInfo,
) -> Project:
    current = (
        await db.execute(
            select(func.count(Project.id)).where(
                Project.owner_id == owner_id, Project.archived_at.is_(None)
            )
        )
    ).scalar_one()
    if current >= MAX_ACTIVE_PROJECTS_PER_USER:
        raise Conflict(
            f"You can have at most {MAX_ACTIVE_PROJECTS_PER_USER} active projects. "
            "Archive one to create another.",
            code="project_limit_reached",
        )
    project = Project(owner_id=owner_id, name=name, description=description or None)
    db.add(project)
    await db.flush()
    record_audit(
        db,
        action="project.create",
        actor_user_id=owner_id,
        project_id=project.id,
        target_type="project",
        target_id=str(project.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return project


async def update_project(
    db: AsyncSession,
    project: Project,
    updates: dict[str, Any],
    *,
    actor_id: uuid.UUID,
    client: ClientInfo,
) -> Project:
    changed: list[str] = []
    if updates.get("name") is not None:
        project.name = updates["name"]
        changed.append("name")
    if "description" in updates:
        project.description = updates["description"] or None
        changed.append("description")
    if changed:
        project.updated_at = utcnow()
        record_audit(
            db,
            action="project.update",
            actor_user_id=actor_id,
            project_id=project.id,
            target_type="project",
            target_id=str(project.id),
            details={"fields": changed},
            ip_address=client.ip,
            user_agent=client.user_agent,
            request_id=client.request_id,
        )
        await db.commit()
    return project


async def archive_project(
    db: AsyncSession, project: Project, *, actor_id: uuid.UUID, client: ClientInfo
) -> None:
    now = utcnow()
    project.archived_at = now
    project.updated_at = now
    record_audit(
        db,
        action="project.archive",
        actor_user_id=actor_id,
        project_id=project.id,
        target_type="project",
        target_id=str(project.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
