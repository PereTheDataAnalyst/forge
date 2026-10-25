"""MCP client management for the project owner (cookie session).

Creating a client makes a project API key that only the MCP endpoint accepts. The key is shown
once. Revoking a client revokes the key, and MCP access stops on the next request.
"""

import uuid

from fastapi import APIRouter, Request

from app.api.deps import AuthDep, ClientDep, DbDep, SettingsDep
from app.core.errors import UnprocessableEntity
from app.modules.mcp import catalog, store
from app.modules.mcp.runtime import McpRuntime
from app.modules.mcp.schemas import (
    McpAccessGroupOut,
    McpCatalogOut,
    McpClientCreate,
    McpClientCreated,
    McpClientListOut,
    McpClientOut,
)
from app.schemas.common import ERROR_RESPONSES
from app.services import project_service

router = APIRouter(tags=["mcp"])


@router.get("/mcp/catalog", response_model=McpCatalogOut, responses=ERROR_RESPONSES)
async def mcp_catalog(request: Request, auth: AuthDep) -> McpCatalogOut:
    """Whether the MCP server is running here, and the kinds of access a client can be given."""
    runtime: McpRuntime = request.app.state.mcp
    return McpCatalogOut(
        state=runtime.state,
        available=runtime.available,
        message=runtime.message,
        endpoint_path=catalog.MCP_PATH,
        groups=[
            McpAccessGroupOut(
                scope=group.scope,
                label=group.label,
                description=group.description,
                tools=list(group.tools),
                read_only=group.read_only,
            )
            for group in catalog.ACCESS_GROUPS
        ],
        default_access=list(catalog.DEFAULT_ACCESS),
        expiry_days=list(catalog.EXPIRY_CHOICES_DAYS),
        default_expiry_days=catalog.DEFAULT_EXPIRY_DAYS,
        max_clients_per_project=catalog.MAX_CLIENTS_PER_PROJECT,
    )


@router.get(
    "/projects/{project_id}/mcp/clients", response_model=McpClientListOut, responses=ERROR_RESPONSES
)
async def list_mcp_clients(project_id: uuid.UUID, auth: AuthDep, db: DbDep) -> McpClientListOut:
    await project_service.get_owned_project(db, auth.user.id, project_id)
    rows = await store.list_clients(db, project_id)
    return McpClientListOut(data=[McpClientOut(**row) for row in rows])


@router.post(
    "/projects/{project_id}/mcp/clients",
    response_model=McpClientCreated,
    status_code=201,
    responses=ERROR_RESPONSES,
)
async def create_mcp_client(
    project_id: uuid.UUID,
    payload: McpClientCreate,
    auth: AuthDep,
    db: DbDep,
    settings: SettingsDep,
    client: ClientDep,
) -> McpClientCreated:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    try:
        access = catalog.normalise_access(payload.access)
    except ValueError as exc:
        raise UnprocessableEntity(str(exc), code="invalid_access") from None
    view, full_key = await store.create_client(
        db,
        settings,
        project,
        actor_id=auth.user.id,
        name=payload.name,
        access=access,
        expires_in_days=payload.expires_in_days,
        client=client,
    )
    return McpClientCreated(**view, key=full_key)


@router.post(
    "/projects/{project_id}/mcp/clients/{client_id}/revoke",
    response_model=McpClientOut,
    responses=ERROR_RESPONSES,
)
async def revoke_mcp_client(
    project_id: uuid.UUID,
    client_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    client: ClientDep,
) -> McpClientOut:
    project = await project_service.get_owned_project(db, auth.user.id, project_id)
    view = await store.revoke_client(db, project, client_id, actor_id=auth.user.id, client=client)
    return McpClientOut(**view)
