"""What the MCP server needs from the rest of the platform, gathered in one place.

Everything optional is a port. A capability whose port is missing is hidden from tool discovery
and answers ``provider_not_configured`` if called anyway, so a half-configured deployment never
pretends to do something it cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from ..observability import NullObserver
from ..ports import AsrProvider, ModelGateway, Observer, RateLimiter, UsageReader
from .audit import AuditRecorder
from .policy import PolicyProvider
from .service import McpTokenService


@runtime_checkable
class ProjectInfoProvider(Protocol):
    """Optional. Returns display information for a project (for example its name and plan).

    Return only fields that are safe to show to anyone holding a token for the project. The
    result is passed through redaction, but the provider is the first line of defence.
    """

    async def get(self, project_id: str) -> Mapping[str, Any] | None: ...


@dataclass
class McpDependencies:
    tokens: McpTokenService
    rate_limiter: RateLimiter
    audit: AuditRecorder
    policy: PolicyProvider
    observer: Observer = NullObserver()  # noqa: RUF009 - a stateless default
    gateway: ModelGateway | None = None
    asr: AsrProvider | None = None
    usage: UsageReader | None = None
    projects: ProjectInfoProvider | None = None
    #: Counts failed authentications. Kept apart from ``rate_limiter`` so a flood of bad tokens
    #: cannot evict the counters that protect real tool calls. Defaults to ``rate_limiter``.
    auth_rate_limiter: RateLimiter | None = None
