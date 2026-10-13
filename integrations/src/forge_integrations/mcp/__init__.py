"""The Forge MCP server: scoped tokens, a gated tool layer and the HTTP application around the SDK.

Names are exported lazily. ``McpTokenService``, the stores, the rate limiter and the audit
recorder need only the standard library, so an admin job that issues or revokes tokens can import
them without installing ``pydantic`` or the ``mcp`` SDK. Importing ``create_mcp_app`` or
``McpToolbox`` is what pulls those in.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers and editors only
    from .audit import AuditRecorder, InMemoryAuditSink
    from .deps import McpDependencies
    from .policy import CachedPolicyProvider, McpPolicy, PolicyProvider, StaticPolicyProvider
    from .principal import McpPrincipal
    from .ratelimit import InMemoryRateLimiter
    from .scopes import ALL_SCOPES, DEFAULT_SCOPES
    from .server import McpHttpApp, build_mcp_server, create_mcp_app
    from .service import McpTokenService
    from .stores import InMemoryMcpClientStore, SqliteMcpClientStore
    from .tools import McpToolbox

_EXPORTS: dict[str, str] = {
    "ALL_SCOPES": ".scopes",
    "DEFAULT_SCOPES": ".scopes",
    "AuditRecorder": ".audit",
    "InMemoryAuditSink": ".audit",
    "InMemoryMcpClientStore": ".stores",
    "InMemoryRateLimiter": ".ratelimit",
    "CachedPolicyProvider": ".policy",
    "McpDependencies": ".deps",
    "McpHttpApp": ".server",
    "McpPolicy": ".policy",
    "McpPrincipal": ".principal",
    "McpTokenService": ".service",
    "McpToolbox": ".tools",
    "PolicyProvider": ".policy",
    "SqliteMcpClientStore": ".stores",
    "StaticPolicyProvider": ".policy",
    "build_mcp_server": ".server",
    "create_mcp_app": ".server",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
