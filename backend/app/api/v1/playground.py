"""Playground endpoints for signed-in users.

The playground calls the same gateway as API keys do, so scope checks, rate limits, request
logs and usage metering are identical. It authenticates with the browser session (cookie plus
CSRF token, enforced inside `get_auth_context`) and never with an API key, so no key ever
reaches the browser. Prompts and replies are returned to the caller and are not stored.
"""

import uuid

from fastapi import APIRouter, Response

from app.api.deps import AuthDep, DbDep, GatewayDep
from app.core.request_context import get_request_id
from app.core.scopes import SCOPE_CHAT_CREATE, SCOPE_MODELS_READ
from app.integrations.contracts import GatewayChatInput, GatewayPrincipal
from app.providers.base import ChatMessage, ModelInfo
from app.schemas.common import ERROR_RESPONSES
from app.schemas.gateway import (
    ChatMessageOut,
    ChatRequestIn,
    ChatResponseOut,
    ModelListOut,
    ModelOut,
    UsageOut,
)
from app.services import project_service

router = APIRouter(prefix="/projects/{project_id}/playground", tags=["playground"])

# A template, not the literal path, so request logs keep a small set of endpoint values.
PLAYGROUND_CHAT_ENDPOINT = "/api/v1/projects/{project_id}/playground/chat"


def _principal(project_id: uuid.UUID, user_id: uuid.UUID) -> GatewayPrincipal:
    """The playground acts for the signed-in owner. Without an API key the gateway meters the
    rate limit per user."""
    return GatewayPrincipal(
        project_id=project_id,
        scopes=frozenset({SCOPE_MODELS_READ, SCOPE_CHAT_CREATE}),
        source="playground",
        user_id=user_id,
    )


def _model_out(model: ModelInfo) -> ModelOut:
    return ModelOut(
        id=model.id,
        kind=model.kind,
        display_name=model.display_name,
        architecture=model.architecture,
        parameters=model.parameters,
        languages=list(model.languages),
        context_length_tokens=model.context_length_tokens,
        license=model.license,
        source_url=model.source_url,
        attribution=model.attribution,
        status=model.status,
    )


@router.get("/models", response_model=ModelListOut, responses=ERROR_RESPONSES)
async def list_playground_models(
    project_id: uuid.UUID,
    response: Response,
    auth: AuthDep,
    db: DbDep,
    gateway: GatewayDep,
) -> ModelListOut:
    """Text models the playground can use, with their live status."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    models, rate_headers = await gateway.list_models(_principal(project_id, auth.user.id))
    response.headers.update(rate_headers)
    return ModelListOut(data=[_model_out(model) for model in models if model.kind == "llm"])


@router.post("/chat", response_model=ChatResponseOut, responses=ERROR_RESPONSES)
async def create_playground_chat(
    project_id: uuid.UUID,
    payload: ChatRequestIn,
    response: Response,
    auth: AuthDep,
    db: DbDep,
    gateway: GatewayDep,
) -> ChatResponseOut:
    """Generate a reply from N-ATLAS for a project you own, using your browser session."""
    await project_service.get_owned_project(db, auth.user.id, project_id)
    output = await gateway.chat(
        _principal(project_id, auth.user.id),
        GatewayChatInput(
            messages=tuple(
                ChatMessage(role=message.role, content=message.content)
                for message in payload.messages
            ),
            model=payload.model,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
            top_p=payload.top_p,
            repetition_penalty=payload.repetition_penalty,
            language=payload.language,
        ),
        request_id=get_request_id() or "unknown",
        endpoint=PLAYGROUND_CHAT_ENDPOINT,
    )
    response.headers.update(output.rate_limit_headers)
    return ChatResponseOut(
        request_id=output.request_id,
        model=output.model,
        message=ChatMessageOut(role="assistant", content=output.text),
        finish_reason=output.finish_reason,
        usage=UsageOut(
            prompt_tokens=output.usage.prompt_tokens,
            completion_tokens=output.usage.completion_tokens,
            total_tokens=output.usage.total_tokens,
        ),
        latency_ms=output.latency_ms,
    )
