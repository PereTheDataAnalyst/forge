"""Ports: the narrow interfaces the advanced integrations need from the core platform.

The core platform owns the gateway, database, Redis, API keys, projects and
observability. These Protocols describe only what Evaluation, Voice and MCP consume, so the
core platform can satisfy them with thin adapters and nothing here redesigns it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    messages: tuple[ChatMessage, ...]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    language: str | None = None
    request_id: str | None = None
    project_id: str | None = None
    #: Who asked, so the gateway can tag its logs: "evaluation", "mcp", "voice" or "api".
    source: str = "api"


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model: str | None = None
    usage: Usage | None = None
    latency_ms: float | None = None  # as reported by the gateway, when available
    finish_reason: str | None = None
    provider_request_id: str | None = None


@runtime_checkable
class ModelGateway(Protocol):
    """Text generation through the N-ATLAS gateway. Raises ``ForgeError`` on failure."""

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    model: str
    duration_seconds: float | None
    latency_ms: float
    request_id: str | None = None


@runtime_checkable
class AsrProvider(Protocol):
    """Speech to text through an official N-ATLAS ASR model. Raises ``ForgeError``."""

    async def transcribe(
        self, *, audio: bytes, audio_format: str, language: str, request_id: str | None = None
    ) -> TranscriptionResult: ...


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


@runtime_checkable
class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window_seconds: int) -> RateDecision: ...


@runtime_checkable
class Observer(Protocol):
    """Receives structured, already-redacted events. Must not block."""

    def emit(self, event: Mapping[str, Any]) -> None: ...


@runtime_checkable
class AuditSink(Protocol):
    async def record(self, event: Mapping[str, Any]) -> None: ...


@runtime_checkable
class UsageReader(Protocol):
    """Read-only project usage summary from the platform's own request logs."""

    async def summary(self, project_id: str, *, days: int) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class AuthContext:
    """Who is calling, as resolved by the platform's own authentication."""

    project_id: str
    principal_id: str | None = None  # API key id or user id
    scopes: frozenset[str] = frozenset()
    label: str | None = None


@runtime_checkable
class ProjectAuthenticator(Protocol):
    """Resolves an ``Authorization`` header to a project using the platform's API keys.

    Returns ``None`` when the credential is missing or invalid. Must be constant time with
    respect to the secret and must never log the credential.
    """

    async def authenticate(self, authorization: str | None, *, remote_addr: str | None = None) -> AuthContext | None: ...


@dataclass(frozen=True)
class McpClientRecord:
    client_id: str
    project_id: str
    name: str
    token_prefix: str
    token_hash: str
    scopes: frozenset[str]
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    def public_view(self) -> dict[str, Any]:
        """Everything safe to show in a dashboard. Never includes the hash."""
        return {
            "client_id": self.client_id,
            "project_id": self.project_id,
            "name": self.name,
            "token_prefix": self.token_prefix,
            "scopes": sorted(self.scopes),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


@runtime_checkable
class McpClientStore(Protocol):
    """Persistence for MCP clients (the ``mcp_clients`` table or equivalent)."""

    async def find_by_prefix(self, prefix: str) -> McpClientRecord | None: ...

    async def touch_last_used(self, client_id: str, when: datetime) -> None: ...

    async def add(self, record: McpClientRecord) -> None: ...

    async def list_for_project(self, project_id: str) -> list[McpClientRecord]: ...

    async def get(self, project_id: str, client_id: str) -> McpClientRecord | None: ...

    async def revoke(self, project_id: str, client_id: str, when: datetime) -> bool: ...
