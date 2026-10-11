"""The developer-facing N-ATLAS gateway. Authenticated with a project API key."""

from fastapi import APIRouter, Response

from app.api.deps import GatewayDep, PrincipalDep
from app.core.request_context import get_request_id
from app.integrations.contracts import GatewayChatInput
from app.providers.base import ChatMessage
from app.schemas.common import ERROR_RESPONSES
from app.schemas.gateway import (
    ChatMessageOut,
    ChatRequestIn,
    ChatResponseOut,
    ModelListOut,
    ModelOut,
    UsageOut,
)

router = APIRouter(tags=["gateway"])


@router.get("/models", response_model=ModelListOut, responses=ERROR_RESPONSES)
async def list_models(
    response: Response, principal: PrincipalDep, gateway: GatewayDep
) -> ModelListOut:
    """List the models available through this gateway. Requires the models.read scope."""
    models, rate_headers = await gateway.list_models(principal)
    response.headers.update(rate_headers)
    return ModelListOut(
        data=[
            ModelOut(
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
            for model in models
        ]
    )


@router.post("/chat", response_model=ChatResponseOut, responses=ERROR_RESPONSES)
async def create_chat(
    payload: ChatRequestIn,
    response: Response,
    principal: PrincipalDep,
    gateway: GatewayDep,
) -> ChatResponseOut:
    """Generate a reply from N-ATLAS. Requires the chat.create scope."""
    output = await gateway.chat(
        principal,
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
        endpoint="/api/v1/chat",
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
