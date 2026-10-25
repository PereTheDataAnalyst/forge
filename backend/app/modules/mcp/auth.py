"""Authenticates MCP requests with platform API keys.

An MCP client is a platform API key that carries the `mcp.invoke` scope, plus a row that names
the client and lists the tools it may use. Both must be in force: revoking the key or the client
stops access on the next request, because nothing is cached except the time of last use.

This class stands in for the library's own token service. It answers the same two questions the
MCP application asks (verify a bearer token, and are you ready) and returns the library's result
type, so the rest of the library is used unchanged.
"""

import hmac
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from forge_integrations.mcp.principal import McpPrincipal
from forge_integrations.mcp.service import (
    REASON_BAD_SECRET,
    REASON_EXPIRED,
    REASON_MALFORMED,
    REASON_REVOKED,
    REASON_UNKNOWN_PREFIX,
    AuthResult,
)

from app.modules.mcp.catalog import INVOKE_SCOPE, scopes_for
from app.security.tokens import hash_api_key, parse_api_key, verify_api_key

def _utc_now() -> datetime:
    return datetime.now(UTC)


# Reasons that only the audit log sees. The caller is always told the same thing.
REASON_PROJECT_ARCHIVED = "project_archived"
REASON_MISSING_SCOPE = "missing_scope"
REASON_NO_CLIENT = "no_client"


@dataclass(frozen=True, slots=True)
class McpCredentials:
    """What is known about a presented key, read in one query."""

    key_id: uuid.UUID
    key_hash: str
    key_scopes: tuple[str, ...]
    key_expires_at: datetime | None
    key_revoked_at: datetime | None
    project_id: uuid.UUID
    project_archived: bool
    client_id: uuid.UUID | None
    client_name: str | None
    client_allowed_tools: tuple[str, ...]
    client_revoked_at: datetime | None
    client_last_seen_at: datetime | None


class CredentialStore(Protocol):
    async def find(self, prefix: str) -> McpCredentials | None: ...

    async def touch(self, client_id: uuid.UUID, key_id: uuid.UUID, when: datetime) -> None: ...

    async def ping(self) -> bool: ...


class PlatformTokenService:
    def __init__(
        self,
        store: CredentialStore,
        pepper: bytes,
        *,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        touch_interval_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._pepper = pepper
        self._clock = clock
        self._monotonic = monotonic
        self._touch_interval = touch_interval_seconds
        self._touched: dict[uuid.UUID, float] = {}

    async def verify(self, token: str) -> AuthResult:
        prefix = parse_api_key(token)
        if prefix is None:
            return AuthResult(None, REASON_MALFORMED)
        found = await self._store.find(prefix)
        if found is None:
            # Spend comparable time so an unknown prefix cannot be told apart by timing.
            hmac.compare_digest(hash_api_key(token, self._pepper), "0" * 64)
            return AuthResult(None, REASON_UNKNOWN_PREFIX, token_prefix=prefix)
        known = {
            "token_prefix": prefix,
            "project_id": str(found.project_id),
            "client_id": str(found.client_id) if found.client_id else None,
        }
        if not verify_api_key(token, found.key_hash, self._pepper):
            return AuthResult(None, REASON_BAD_SECRET, **known)
        now = self._clock()
        if found.key_revoked_at is not None:
            return AuthResult(None, REASON_REVOKED, **known)
        if found.key_expires_at is not None and found.key_expires_at <= now:
            return AuthResult(None, REASON_EXPIRED, **known)
        if found.project_archived:
            return AuthResult(None, REASON_PROJECT_ARCHIVED, **known)
        if INVOKE_SCOPE not in found.key_scopes:
            return AuthResult(None, REASON_MISSING_SCOPE, **known)
        if found.client_id is None:
            return AuthResult(None, REASON_NO_CLIENT, **known)
        if found.client_revoked_at is not None:
            return AuthResult(None, REASON_REVOKED, **known)
        await self._touch(found.client_id, found.key_id, now)
        return AuthResult(
            McpPrincipal(
                project_id=str(found.project_id),
                client_id=str(found.client_id),
                client_name=found.client_name or "",
                token_prefix=prefix,
                scopes=scopes_for(found.client_allowed_tools),
                expires_at=found.key_expires_at,
                last_used_at=found.client_last_seen_at,
            ),
            **known,
        )

    async def _touch(self, client_id: uuid.UUID, key_id: uuid.UUID, now: datetime) -> None:
        """Record last use at most once a minute so reads do not turn into writes."""
        tick = self._monotonic()
        previous = self._touched.get(client_id)
        if previous is not None and tick - previous < self._touch_interval:
            return
        if len(self._touched) > 10_000:
            self._touched.clear()
        self._touched[client_id] = tick
        try:
            await self._store.touch(client_id, key_id, now)
        except Exception:
            # Bookkeeping must not reject a valid key. Try again on the next request.
            self._touched.pop(client_id, None)

    async def ping(self) -> bool:
        """True when the credential store answers. Used by the MCP readiness probe."""
        return await self._store.ping()
