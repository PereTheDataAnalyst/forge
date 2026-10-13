"""The authenticated caller of an MCP request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Key under which the authentication middleware stores the principal in ``scope["state"]``.
STATE_KEY = "forge.mcp.principal"


@dataclass(frozen=True)
class McpPrincipal:
    project_id: str
    client_id: str
    client_name: str
    token_prefix: str
    scopes: frozenset[str]
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def principal_from_scope(scope: Any) -> McpPrincipal | None:
    """Read the principal the authentication middleware attached to an ASGI scope."""
    if not isinstance(scope, dict):
        return None
    state = scope.get("state")
    value = state.get(STATE_KEY) if isinstance(state, dict) else None
    return value if isinstance(value, McpPrincipal) else None


def principal_from_request(request: Any) -> McpPrincipal | None:
    """Read the principal from a Starlette ``Request`` (or anything exposing ``.scope``)."""
    return principal_from_scope(getattr(request, "scope", None))
