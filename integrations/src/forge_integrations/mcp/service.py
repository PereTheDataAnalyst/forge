"""MCP token lifecycle: issue, verify, list, revoke.

Tokens are scoped, expire, and are shown once. Only an HMAC of each token is stored. Verification
reports *why* it failed to the caller (for the audit log) but the caller must never forward that
reason to the client: every failure looks the same from outside.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from ..config import McpSettings
from ..errors import ErrorCode, ForgeError, ValidationFailed, ValidationIssue
from ..ports import McpClientRecord, McpClientStore
from ..security.tokens import TOKEN_KIND_MCP, TokenHasher, generate_token, parse_token
from .audit import EVENT_TOKEN_ISSUED, EVENT_TOKEN_REVOKED, AuditRecorder, utc_now
from .principal import McpPrincipal
from .scopes import DEFAULT_SCOPES, normalize_scopes

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_NAME_CHARS = 80
_MAX_ISSUE_ATTEMPTS = 5

REASON_MALFORMED = "malformed"
REASON_WRONG_KIND = "wrong_kind"
REASON_UNKNOWN_PREFIX = "unknown_prefix"
REASON_BAD_SECRET = "bad_secret"
REASON_REVOKED = "revoked"
REASON_EXPIRED = "expired"


@dataclass(frozen=True)
class IssuedToken:
    """The plaintext token is only ever available here, once, at creation."""

    token: str
    record: McpClientRecord

    def __repr__(self) -> str:  # keep the secret out of accidental logs and tracebacks
        return f"IssuedToken(prefix={self.record.token_prefix!r}, client_id={self.record.client_id!r})"


@dataclass(frozen=True)
class AuthResult:
    principal: McpPrincipal | None
    reason: str | None = None
    token_prefix: str | None = None
    project_id: str | None = None
    client_id: str | None = None


class McpTokenService:
    def __init__(
        self,
        store: McpClientStore,
        hasher: TokenHasher,
        settings: McpSettings,
        *,
        audit: AuditRecorder | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        last_used_interval_seconds: float = 60.0,
    ) -> None:
        self._store = store
        self._hasher = hasher
        self._settings = settings
        self._audit = audit or AuditRecorder()
        self._clock = clock
        self._monotonic = monotonic
        self._touch_interval = last_used_interval_seconds
        self._touched: dict[str, float] = {}

    # -- issue ----------------------------------------------------------------------------
    async def issue(
        self,
        *,
        project_id: str,
        name: str,
        scopes: Iterable[object] | None = None,
        ttl_days: int | None = None,
        actor: str | None = None,
    ) -> IssuedToken:
        issues: list[ValidationIssue] = []
        if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
            issues.append(ValidationIssue("project_id", "must be 1 to 64 characters: letters, digits and _ . : -"))
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_name or len(clean_name) > MAX_NAME_CHARS or _CONTROL_RE.search(clean_name):
            issues.append(ValidationIssue("name", f"must be 1 to {MAX_NAME_CHARS} printable characters"))
        scope_set = DEFAULT_SCOPES
        if scopes is not None:
            try:
                scope_set = normalize_scopes(scopes)
            except ValidationFailed as exc:
                issues.extend(exc.issues)
        ttl = self._settings.token_default_ttl_days if ttl_days is None else ttl_days
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= self._settings.token_max_ttl_days:
            issues.append(
                ValidationIssue("ttl_days", f"must be a whole number from 1 to {self._settings.token_max_ttl_days}")
            )
        if issues:
            raise ValidationFailed(issues)

        now = self._clock()
        for _ in range(_MAX_ISSUE_ATTEMPTS):
            generated = generate_token(TOKEN_KIND_MCP)
            record = McpClientRecord(
                client_id=str(uuid.uuid4()),
                project_id=project_id,
                name=clean_name,
                token_prefix=generated.prefix,
                token_hash=self._hasher.hash(generated.token),
                scopes=scope_set,
                created_at=now,
                expires_at=now + timedelta(days=ttl),
            )
            try:
                await self._store.add(record)
            except ForgeError as exc:
                if exc.code == ErrorCode.CONFLICT:
                    continue  # prefix collision: draw a new token
                raise
            await self._audit.record(
                EVENT_TOKEN_ISSUED,
                project_id=project_id,
                client_id=record.client_id,
                token_prefix=record.token_prefix,
                scopes=sorted(record.scopes),
                expires_at=record.expires_at.isoformat() if record.expires_at else None,
                actor=actor,
            )
            return IssuedToken(token=generated.token, record=record)
        raise ForgeError(ErrorCode.INTERNAL, "Could not allocate a unique token. Try again.", http_status=500, retryable=True)

    # -- verify ---------------------------------------------------------------------------
    async def verify(self, token: str) -> AuthResult:
        parsed = parse_token(token)
        if parsed is None:
            return AuthResult(None, REASON_MALFORMED)
        if parsed.kind != TOKEN_KIND_MCP:
            self._hasher.burn(token)
            return AuthResult(None, REASON_WRONG_KIND)
        record = await self._store.find_by_prefix(parsed.prefix)
        if record is None:
            self._hasher.burn(token)
            return AuthResult(None, REASON_UNKNOWN_PREFIX, token_prefix=parsed.prefix)
        known = dict(token_prefix=record.token_prefix, project_id=record.project_id, client_id=record.client_id)
        if not self._hasher.verify(token, record.token_hash):
            return AuthResult(None, REASON_BAD_SECRET, **known)
        now = self._clock()
        if record.revoked_at is not None:
            return AuthResult(None, REASON_REVOKED, **known)
        if record.expires_at is not None and record.expires_at <= now:
            return AuthResult(None, REASON_EXPIRED, **known)
        await self._touch(record.client_id, now)
        return AuthResult(
            McpPrincipal(
                project_id=record.project_id,
                client_id=record.client_id,
                client_name=record.name,
                token_prefix=record.token_prefix,
                scopes=record.scopes,
                expires_at=record.expires_at,
                last_used_at=record.last_used_at,
            ),
            **known,
        )

    async def _touch(self, client_id: str, now: datetime) -> None:
        """Record last use at most once per interval so reads do not turn into writes."""
        tick = self._monotonic()
        previous = self._touched.get(client_id)
        if previous is not None and tick - previous < self._touch_interval:
            return
        if len(self._touched) > 10_000:
            self._touched.clear()
        self._touched[client_id] = tick
        try:
            await self._store.touch_last_used(client_id, now)
        except Exception:  # noqa: BLE001 - a bookkeeping failure must not reject a valid token
            self._touched.pop(client_id, None)

    async def ping(self) -> bool:
        """``True`` when the client store answers. Used by the readiness probe."""
        try:
            await self._store.find_by_prefix("00000000")
            return True
        except Exception:  # noqa: BLE001 - any failure means not ready
            return False

    # -- list and revoke ------------------------------------------------------------------
    async def list_clients(self, project_id: str) -> list[dict[str, Any]]:
        self._require_project_id(project_id)
        return [record.public_view() for record in await self._store.list_for_project(project_id)]

    async def revoke(self, project_id: str, client_id: str, *, actor: str | None = None) -> bool:
        """Revoke a client token. Returns ``True`` when this call revoked it and ``False`` when it was
        already revoked. Raises ``NOT_FOUND`` when the client does not exist in the project."""
        self._require_project_id(project_id)
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 64:
            raise ForgeError(ErrorCode.NOT_FOUND, "MCP client not found.", http_status=404)
        revoked = await self._store.revoke(project_id, client_id, self._clock())
        if revoked:
            await self._audit.record(EVENT_TOKEN_REVOKED, project_id=project_id, client_id=client_id, actor=actor)
            self._touched.pop(client_id, None)
            return True
        if await self._store.get(project_id, client_id) is None:
            raise ForgeError(ErrorCode.NOT_FOUND, "MCP client not found.", http_status=404)
        return False

    @staticmethod
    def _require_project_id(project_id: str) -> None:
        if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
            raise ValidationFailed([ValidationIssue("project_id", "must be 1 to 64 characters: letters, digits and _ . : -")])
