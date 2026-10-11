"""Project management for signed-in users."""

import uuid

from fastapi import APIRouter, Response

from app.api.deps import AuthDep, ClientDep, DbDep
from app.db.models.project import Project
from app.schemas.common import ERROR_RESPONSES
from app.schemas.projects import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


def _out(project: Project, active_key_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_key_count=active_key_count,
    )


@router.get("", response_model=ProjectList, responses=ERROR_RESPONSES)
async def list_projects(auth: AuthDep, db: DbDep) -> ProjectList:
    rows = await project_service.list_projects(db, auth.user.id)
    return ProjectList(data=[_out(project, count) for project, count in rows])


@router.post("", response_model=ProjectOut, status_code=201, responses=ERROR_RESPONSES)
async def create_project(
    payload: ProjectCreate, auth: AuthDep, db: DbDep, client: ClientDep
) -> ProjectOut:
    project = await project_service.create_project(
        db, auth.user.id, name=payload.name, description=payload.description, client=client
    )
    return _out(project, 0)


@router.get("/{project_id}", response_model=ProjectOut, responses=ERROR_RESPONSES)
async def get_project(project_id: uuid.UUID, auth: AuthDep, db: DbDep) -> ProjectOut:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    return _out(project, await project_service.count_active_keys(db, project.id))


@router.patch("/{project_id}", response_model=ProjectOut, responses=ERROR_RESPONSES)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, auth: AuthDep, db: DbDep, client: ClientDep
) -> ProjectOut:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    project = await project_service.update_project(
        db,
        project,
        payload.model_dump(exclude_unset=True),
        actor_id=auth.user.id,
        client=client,
    )
    return _out(project, await project_service.count_active_keys(db, project.id))


@router.delete(
    "/{project_id}", status_code=204, response_class=Response, responses=ERROR_RESPONSES
)
async def archive_project(
    project_id: uuid.UUID, auth: AuthDep, db: DbDep, client: ClientDep
) -> Response:
    """Archive a project. Its API keys stop working immediately."""
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    await project_service.archive_project(db, project, actor_id=auth.user.id, client=client)
    return Response(status_code=204)
