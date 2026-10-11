"""Contracts between the core platform (Agent 1) and extension modules (Agent 2).

Extension modules (evaluation, voice, MCP) must call N-ATLAS through `ChatGateway` so that
authentication scope checks, rate limits, request logs and usage metering stay consistent.
They obtain the gateway from `request.app.state.gateway`.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.base import AsrProvider, ChatMessage, FinishReason, Usage

RequestSource = Literal["api_key", "playground", "mcp", "evaluation", "voice"]


@dataclass(frozen=True, slots=True)
class GatewayPrincipal:
    """The authenticated caller of the gateway, always bound to exactly one project."""

    project_id: uuid.UUID
    scopes: frozenset[str]
    source: RequestSource
    api_key_id: uuid.UUID | None = None
    api_key_prefix: str | None = None
    user_id: uuid.UUID | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class GatewayChatInput:
    messages: tuple[ChatMessage, ...]
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    repetition_penalty: float | None = None
    language: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayChatOutput:
    request_id: str
    model: str
    text: str
    finish_reason: FinishReason
    usage: Usage
    latency_ms: int
    rate_limit_headers: dict[str, str] = field(default_factory=dict)


class ChatGateway(Protocol):
    async def chat(
        self,
        principal: GatewayPrincipal,
        payload: GatewayChatInput,
        *,
        request_id: str,
        endpoint: str,
        method: str = "POST",
    ) -> GatewayChatOutput: ...


# Signature: (session, user_id, limit) -> list of JSON-serialisable dictionaries with keys
# id, evaluation_name, status, created_at (ISO 8601), model.
RecentEvaluationRuns = Callable[[AsyncSession, uuid.UUID, int], Awaitable[list[dict[str, Any]]]]


@dataclass
class IntegrationRegistry:
    """Optional capabilities registered by extension modules during `register(app)`."""

    asr_provider: AsrProvider | None = None
    recent_evaluation_runs: RecentEvaluationRuns | None = None
    extras: dict[str, Any] = field(default_factory=dict)
