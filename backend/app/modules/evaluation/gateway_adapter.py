"""Lets the evaluation engine call the platform gateway.

Every attempt of an evaluation run is a normal gateway request: scope check, rate limit,
request log and usage metering are the same as for any other caller. The request is logged
with source `evaluation`. It is tied to the project, not to a person or an API key, so the
rate limit is metered per project.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.ports import GenerationRequest, GenerationResult, Usage

from app.core.errors import AppError
from app.core.scopes import SCOPE_CHAT_CREATE
from app.integrations.contracts import ChatGateway, GatewayChatInput, GatewayPrincipal
from app.modules.evaluation.errors import to_forge_error
from app.providers.base import ChatMessage

# A template, not a literal path, so request logs keep a small set of endpoint values.
EVALUATION_ENDPOINT = "/api/v1/projects/{project_id}/evaluation-runs"

# A throttled call is not logged and costs nothing, so a short wait and another try is the
# honest behaviour. After these tries the case is recorded with the `rate_limited` code.
RATE_LIMIT_RETRIES = 3
MAX_WAIT_SECONDS = 15.0


def _retry_after_seconds(exc: AppError) -> float:
    try:
        wait = float(exc.headers.get("Retry-After", "1"))
    except ValueError:
        wait = 1.0
    return min(max(wait, 1.0), MAX_WAIT_SECONDS)


def _project_id(request: GenerationRequest) -> uuid.UUID:
    if not request.project_id:
        raise ForgeError(ErrorCode.INTERNAL, "The evaluation call has no project.", http_status=500)
    try:
        return uuid.UUID(request.project_id)
    except ValueError as exc:
        raise ForgeError(
            ErrorCode.INTERNAL, "The evaluation call has an invalid project.", http_status=500
        ) from exc


class PlatformModelGateway:
    """The library's `ModelGateway` port, implemented with the platform's gateway.

    The gateway is looked up on every call, never captured, so replacing
    `app.state.gateway` (as the integration tests do) takes effect immediately.
    """

    def __init__(
        self,
        get_gateway: Callable[[], ChatGateway],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._get_gateway = get_gateway
        self._sleep = sleep

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        principal = GatewayPrincipal(
            project_id=_project_id(request),
            scopes=frozenset({SCOPE_CHAT_CREATE}),
            source="evaluation",
        )
        payload = GatewayChatInput(
            messages=tuple(
                ChatMessage(role=message.role, content=message.content)
                for message in request.messages
            ),
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            language=request.language,
        )
        request_id = request.request_id or uuid.uuid4().hex

        waits = 0
        while True:
            try:
                output = await self._get_gateway().chat(
                    principal, payload, request_id=request_id, endpoint=EVALUATION_ENDPOINT
                )
            except AppError as exc:
                if exc.code == "rate_limited" and waits < RATE_LIMIT_RETRIES:
                    waits += 1
                    await self._sleep(_retry_after_seconds(exc))
                    continue
                raise to_forge_error(exc) from exc
            return GenerationResult(
                text=output.text,
                model=output.model,
                usage=Usage(
                    prompt_tokens=output.usage.prompt_tokens,
                    completion_tokens=output.usage.completion_tokens,
                    total_tokens=output.usage.total_tokens,
                ),
                latency_ms=float(output.latency_ms),
                finish_reason=output.finish_reason,
            )
