"""HTTP routes of the Evaluation Lab.

They serve the web app, so they use the browser session (cookie plus CSRF, enforced inside
`get_auth_context`). No API key is involved. Every route first proves that the project belongs
to the signed-in user: a missing, archived or foreign project is a 404.
"""

import re
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from forge_integrations.errors import ForgeError
from forge_integrations.evaluation import describe_starter_datasets, load_starter_dataset
from forge_integrations.evaluation.catalogue import check_catalogue
from forge_integrations.evaluation.export import export_csv, export_json
from forge_integrations.evaluation.models import RunConfig
from forge_integrations.evaluation.service import STATE_RUNNING
from forge_integrations.evaluation.validation import validate_dataset

from app.api.deps import AuthDep, ClientDep, DbDep
from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ServiceUnavailable
from app.core.scopes import SCOPE_MODELS_READ
from app.integrations.contracts import GatewayPrincipal
from app.modules.evaluation import service as datasets
from app.modules.evaluation.errors import to_app_error
from app.modules.evaluation.mapping import (
    case_to_dict,
    dataset_limits,
    evaluation_to_dict,
    rows_to_dataset,
    run_list_item,
)
from app.modules.evaluation.runtime import EvaluationRuntime
from app.modules.evaluation.schemas import EvaluationCreateIn, RunStartIn
from app.schemas.common import ERROR_RESPONSES
from app.services import project_service
from app.services.audit import record_audit

router = APIRouter(tags=["evaluations"])

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_LISTED_RUNS = 50
_UTF8_BOM = "\ufeff"  # Excel needs it to read Hausa, Igbo and Yoruba text in a CSV correctly.


def _runtime(request: Request) -> EvaluationRuntime:
    runtime = getattr(request.app.state, "evaluation", None)
    if runtime is None:
        raise ServiceUnavailable(
            "The evaluation service is not running.", code="evaluation_unavailable"
        )
    return runtime


def _run_key(value: str) -> str:
    if not _RUN_ID.match(value):
        raise NotFound("Evaluation run not found.", code="evaluation_run_not_found")
    return value


def _detail(evaluation: Any, case_rows: list[Any]) -> dict[str, Any]:
    return {
        **evaluation_to_dict(evaluation, len(case_rows)),
        "cases": [case_to_dict(row) for row in case_rows],
    }


async def _require_available_model(request: Request, project_id: uuid.UUID, model_id: str) -> None:
    """Refuse to start a run that could only produce errors, and say why."""
    principal = GatewayPrincipal(
        project_id=project_id, scopes=frozenset({SCOPE_MODELS_READ}), source="evaluation"
    )
    models, _ = await request.app.state.gateway.list_models(principal)
    model = next((item for item in models if item.kind == "llm" and item.id == model_id), None)
    if model is None:
        raise NotFound("That model is not available here.", code="model_not_found")
    if model.status != "available":
        raise ServiceUnavailable(
            "N-ATLAS is not connected right now. Start the model host, then run the "
            "evaluation again.",
            code="provider_unavailable",
        )


# -- Starter datasets ----------------------------------------------------------------------


@router.get("/evaluations/starters", responses=ERROR_RESPONSES)
async def list_starters(auth: AuthDep) -> dict[str, Any]:
    """Example datasets that ship with Forge, without their prompts."""
    return {"data": describe_starter_datasets()}


@router.get("/evaluations/checks", responses=ERROR_RESPONSES)
async def list_checks(auth: AuthDep) -> dict[str, Any]:
    """The check types a dataset can use, with their parameters. It comes straight from the
    engine, so the help shown in the web app cannot drift from what the engine accepts."""
    return {"data": check_catalogue()}


# -- Datasets ------------------------------------------------------------------------------


@router.get("/projects/{project_id}/evaluations", responses=ERROR_RESPONSES)
async def list_evaluations(project_id: uuid.UUID, auth: AuthDep, db: DbDep) -> dict[str, Any]:
    await project_service.get_owned_project(db, auth.user.id, project_id)
    rows = await datasets.list_evaluations(db, project_id)
    return {"data": [evaluation_to_dict(evaluation, count) for evaluation, count in rows]}


@router.post("/projects/{project_id}/evaluations", status_code=201, responses=ERROR_RESPONSES)
async def create_evaluation(
    project_id: uuid.UUID,
    payload: EvaluationCreateIn,
    request: Request,
    auth: AuthDep,
    db: DbDep,
    client: ClientDep,
) -> dict[str, Any]:
    """Create a dataset from a starter or from a document. The document is validated first."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    settings: Settings = request.app.state.settings
    limits = dataset_limits(settings.eval_max_cases)
    try:
        if payload.starter_id is not None:
            dataset = load_starter_dataset(payload.starter_id, limits=limits)
        else:
            dataset = validate_dataset(payload.dataset, limits=limits)
    except ForgeError as exc:
        raise to_app_error(exc) from exc
    evaluation, cases = await datasets.create_evaluation(
        db,
        project_id=project_id,
        user_id=auth.user.id,
        dataset=dataset,
        max_per_project=settings.eval_max_evaluations_per_project,
        client=client,
    )
    return _detail(evaluation, cases)


@router.get("/projects/{project_id}/evaluations/{evaluation_id}", responses=ERROR_RESPONSES)
async def get_evaluation(
    project_id: uuid.UUID, evaluation_id: uuid.UUID, auth: AuthDep, db: DbDep
) -> dict[str, Any]:
    await project_service.get_owned_project(db, auth.user.id, project_id)
    evaluation = await datasets.get_evaluation(db, project_id, evaluation_id)
    return _detail(evaluation, await datasets.load_cases(db, evaluation.id))


@router.delete(
    "/projects/{project_id}/evaluations/{evaluation_id}",
    status_code=204,
    response_class=Response,
    responses=ERROR_RESPONSES,
)
async def archive_evaluation(
    project_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    auth: AuthDep,
    db: DbDep,
    client: ClientDep,
) -> Response:
    """Archive a dataset. Its past runs stay readable."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    evaluation = await datasets.get_evaluation(db, project_id, evaluation_id)
    await datasets.archive_evaluation(db, evaluation, user_id=auth.user.id, client=client)
    return Response(status_code=204)


# -- Runs ----------------------------------------------------------------------------------


@router.post("/projects/{project_id}/evaluation-runs", status_code=202, responses=ERROR_RESPONSES)
async def start_run(
    project_id: uuid.UUID,
    payload: RunStartIn,
    request: Request,
    auth: AuthDep,
    db: DbDep,
    client: ClientDep,
) -> dict[str, Any]:
    """Start a run in the background and return at once. Poll the run for progress."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    runtime = _runtime(request)
    settings: Settings = request.app.state.settings

    evaluation = await datasets.get_evaluation(db, project_id, payload.evaluation_id)
    cases = await datasets.load_cases(db, evaluation.id)
    dataset = rows_to_dataset(evaluation, cases)
    model_id = payload.model or settings.natlas_model_id
    await _require_available_model(request, project_id, model_id)
    await request.app.state.rate_limiter.enforce(
        f"rl:eval:start:user:{auth.user.id}",
        settings.eval_runs_per_hour,
        3600,
        code="evaluation_rate_limited",
        message=f"You can start {settings.eval_runs_per_hour} evaluation runs per hour. "
        "Try again later.",
    )

    try:
        handle = await runtime.manager.start(
            str(project_id),
            dataset,
            RunConfig(
                model=model_id,
                prompt_version=payload.prompt_version,
                max_concurrency=settings.eval_max_concurrency,
                call_timeout_seconds=settings.eval_call_timeout_seconds,
            ),
        )
    except ForgeError as exc:
        raise to_app_error(exc) from exc

    record_audit(
        db,
        action="evaluation.run.start",
        actor_user_id=auth.user.id,
        project_id=project_id,
        target_type="evaluation_run",
        target_id=handle.run_id,
        details={"evaluation_id": str(evaluation.id), "cases": len(cases), "model": model_id},
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return handle.to_dict()


@router.get("/projects/{project_id}/evaluation-runs", responses=ERROR_RESPONSES)
async def list_runs(
    project_id: uuid.UUID, request: Request, auth: AuthDep, db: DbDep
) -> dict[str, Any]:
    """Runs in progress first, then finished runs, newest first."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    items = await _runtime(request).manager.list_runs(str(project_id), limit=MAX_LISTED_RUNS)
    return {"data": [run_list_item(item) for item in items]}


@router.get("/projects/{project_id}/evaluation-runs/{run_id}", responses=ERROR_RESPONSES)
async def get_run(
    project_id: uuid.UUID, run_id: str, request: Request, auth: AuthDep, db: DbDep
) -> dict[str, Any]:
    """Progress while running, then the full result. After a service restart a run that was
    saved is still found in the database."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    key = _run_key(run_id)
    manager = _runtime(request).manager
    handle = manager.status(str(project_id), key)
    run = None
    if handle is None or handle.state != STATE_RUNNING:
        run = await manager.get_run(str(project_id), key)
    if handle is None and run is None:
        raise NotFound("Evaluation run not found.", code="evaluation_run_not_found")
    return {
        "state": handle.state if handle is not None else run.status,
        "handle": handle.to_dict() if handle is not None else None,
        "run": run.to_dict() if run is not None else None,
    }


@router.post(
    "/projects/{project_id}/evaluation-runs/{run_id}/cancel",
    status_code=202,
    responses=ERROR_RESPONSES,
)
async def cancel_run(
    project_id: uuid.UUID, run_id: str, request: Request, auth: AuthDep, db: DbDep
) -> dict[str, Any]:
    """Ask a run to stop. Cases already running finish; the rest are skipped."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    key = _run_key(run_id)
    manager = _runtime(request).manager
    if await manager.cancel(str(project_id), key):
        return {"cancelling": True}
    if manager.status(str(project_id), key) is None and (
        await manager.get_run(str(project_id), key) is None
    ):
        raise NotFound("Evaluation run not found.", code="evaluation_run_not_found")
    raise Conflict("This run is not in progress.", code="evaluation_run_not_running")


@router.get("/projects/{project_id}/evaluation-runs/{run_id}/export", responses=ERROR_RESPONSES)
async def export_run(
    project_id: uuid.UUID,
    run_id: str,
    request: Request,
    auth: AuthDep,
    db: DbDep,
    file_format: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
) -> Response:
    """Download a finished run as CSV or JSON. The file contains model outputs, so it is never
    cached."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    key = _run_key(run_id)
    manager = _runtime(request).manager
    run = await manager.get_run(str(project_id), key)
    if run is None:
        handle = manager.status(str(project_id), key)
        if handle is not None and handle.state == STATE_RUNNING:
            raise Conflict(
                "The run is still in progress. Export it when it has finished.",
                code="evaluation_run_not_finished",
            )
        raise NotFound("Evaluation run not found.", code="evaluation_run_not_found")
    if file_format == "csv":
        body, media_type = _UTF8_BOM + export_csv(run), "text/csv; charset=utf-8"
    else:
        body, media_type = export_json(run), "application/json"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="forge-evaluation-{key[:8]}.{file_format}"',
            "Cache-Control": "no-store",
        },
    )
