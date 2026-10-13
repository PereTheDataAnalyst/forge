"""Organization and project controls for MCP.

A project administrator can switch MCP off for a project or narrow the scopes its tokens
may use without revoking tokens one by one. The effective scopes of a call are the token's
scopes intersected with the project's policy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from .scopes import ALL_SCOPES


@dataclass(frozen=True)
class McpPolicy:
    enabled: bool = True
    allowed_scopes: frozenset[str] = field(default_factory=lambda: ALL_SCOPES)

    def effective(self, token_scopes: frozenset[str]) -> frozenset[str]:
        if not self.enabled:
            return frozenset()
        return token_scopes & self.allowed_scopes


@runtime_checkable
class PolicyProvider(Protocol):
    async def get(self, project_id: str) -> McpPolicy: ...


class StaticPolicyProvider:
    """In-memory policies with a default. Useful for tests and standalone use."""

    def __init__(self, default: McpPolicy | None = None, per_project: dict[str, McpPolicy] | None = None) -> None:
        self._default = default or McpPolicy()
        self._per_project = dict(per_project or {})

    def set(self, project_id: str, policy: McpPolicy) -> None:
        self._per_project[project_id] = policy

    async def get(self, project_id: str) -> McpPolicy:
        return self._per_project.get(project_id, self._default)


class CachedPolicyProvider:
    """Wraps a slower provider (for example a database lookup) with a short cache."""

    def __init__(self, inner: PolicyProvider, *, ttl_seconds: float = 10.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, McpPolicy]] = {}

    async def get(self, project_id: str) -> McpPolicy:
        now = self._clock()
        hit = self._cache.get(project_id)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        policy = await self._inner.get(project_id)
        if len(self._cache) > 10_000:
            self._cache.clear()
        self._cache[project_id] = (now, policy)
        return policy
