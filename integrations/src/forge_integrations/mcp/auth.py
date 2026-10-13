"""Bearer-token authentication for the MCP endpoint, as pure ASGI middleware.

What it guarantees:

  * exactly one ``Authorization: Bearer <token>`` header is accepted; anything else is a 401;
  * every failure looks identical to the client (same status, same body, no reason), while the
    audit log records the real reason and the token prefix (never the token);
  * failed attempts are counted per client address. Past the limit the answer is 429 with
    ``Retry-After``, which protects the token store and the log from a flood. A valid token is
    never locked out by other callers' failures;
  * after authentication the credential is removed from the request, so no handler, log line or
    library further down can see it, and the verified principal is attached to ``scope["state"]``.
"""

from __future__ import annotations

import time
from typing import Callable

from ..errors import ErrorCode, ForgeError
from ..ports import RateLimiter
from ..web.asgi import ASGIApp, Receive, Scope, Send, client_address, get_all_headers, request_id_for, send_error
from .audit import EVENT_AUTH_FAILURE, EVENT_AUTH_THROTTLED, EVENT_REQUEST_LIMITED, AuditRecorder
from .principal import STATE_KEY, principal_from_scope
from .service import REASON_MALFORMED, McpTokenService

REASON_NO_HEADER = "no_authorization_header"
REASON_BAD_HEADER = "bad_authorization_header"
REASON_MULTIPLE_HEADERS = "multiple_authorization_headers"

_MAX_TOKEN_CHARS = 200
_THROTTLE_LOG_INTERVAL_SECONDS = 30.0


def _unauthorized(token_presented: bool) -> ForgeError:
    challenge = 'Bearer realm="forge-mcp"' + (', error="invalid_token"' if token_presented else "")
    return ForgeError(
        ErrorCode.UNAUTHORIZED,
        "A valid bearer token is required.",
        http_status=401,
        headers={"WWW-Authenticate": challenge},
    )


def _extract_bearer(values: list[str]) -> tuple[str | None, str | None]:
    """Return ``(token, failure_reason)``."""
    if not values:
        return None, REASON_NO_HEADER
    if len(values) > 1:
        return None, REASON_MULTIPLE_HEADERS
    scheme, separator, rest = values[0].partition(" ")
    token = rest.strip()
    if scheme.lower() != "bearer" or not separator or not token or " " in token or len(token) > _MAX_TOKEN_CHARS:
        return None, REASON_BAD_HEADER
    return token, None


class McpAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        tokens: McpTokenService,
        audit: AuditRecorder,
        limiter: RateLimiter,
        failures_per_minute: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._tokens = tokens
        self._audit = audit
        self._limiter = limiter
        self._limit = failures_per_minute
        self._monotonic = monotonic
        self._last_throttle_log: dict[str, float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = request_id_for(scope)
        token, header_problem = _extract_bearer(get_all_headers(scope, "authorization"))
        if token is None:
            await self._reject(scope, send, rid, header_problem or REASON_BAD_HEADER, presented=header_problem != REASON_NO_HEADER)
            return

        result = await self._tokens.verify(token)
        if result.principal is None:
            await self._reject(
                scope, send, rid, result.reason or REASON_MALFORMED, presented=True, prefix=result.token_prefix, project_id=result.project_id
            )
            return

        scope.setdefault("state", {})[STATE_KEY] = result.principal
        # The credential is spent. Nothing downstream needs it, so nothing downstream gets it.
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"authorization"]
        await self.app(scope, receive, send)

    async def _reject(
        self,
        scope: Scope,
        send: Send,
        rid: str,
        reason: str,
        *,
        presented: bool,
        prefix: str | None = None,
        project_id: str | None = None,
    ) -> None:
        address = client_address(scope) or "unknown"
        try:
            decision = await self._limiter.check(f"mcp:authfail:{address}", self._limit, 60)
            throttled = not decision.allowed
            retry_after = decision.retry_after_seconds
        except Exception:  # noqa: BLE001 - the request is being rejected anyway; do not turn a limiter fault into a 500
            throttled, retry_after = False, 0

        if throttled:
            await self._log_throttle(address, rid)
            await send_error(
                send,
                ForgeError(
                    ErrorCode.RATE_LIMITED,
                    "Too many failed authentication attempts. Retry later.",
                    http_status=429,
                    retryable=True,
                    headers={"Retry-After": str(max(1, retry_after))},
                ),
                request_id=rid,
            )
            return

        await self._audit.record(
            EVENT_AUTH_FAILURE,
            request_id=rid,
            reason=reason,
            token_prefix=prefix,
            project_id=project_id,
            client_address=address,
        )
        await send_error(send, _unauthorized(presented), request_id=rid)

    async def _log_throttle(self, address: str, rid: str) -> None:
        """At most one throttle event per address per interval, so an attack cannot flood the log."""
        now = self._monotonic()
        last = self._last_throttle_log.get(address)
        if last is not None and now - last < _THROTTLE_LOG_INTERVAL_SECONDS:
            return
        if len(self._last_throttle_log) > 10_000:
            self._last_throttle_log.clear()
        self._last_throttle_log[address] = now
        await self._audit.record(EVENT_AUTH_THROTTLED, request_id=rid, client_address=address)


class McpRequestRateLimitMiddleware:
    """Caps the requests of any kind one token may make per minute.

    This sits after authentication and covers the cheap protocol calls (discovery, listing, ping)
    that the per-tool limits do not, so one token cannot turn them into load on the token store.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        audit: AuditRecorder,
        requests_per_minute: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._audit = audit
        self._limit = requests_per_minute
        self._monotonic = monotonic
        self._last_log: dict[str, float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        principal = principal_from_scope(scope)
        if principal is None:
            # Never run requests unmetered because the stack was assembled in the wrong order.
            raise RuntimeError("McpRequestRateLimitMiddleware must run after McpAuthMiddleware")
        rid = request_id_for(scope)
        try:
            decision = await self._limiter.check(f"mcp:req:{principal.client_id}", self._limit, 60)
        except Exception:  # noqa: BLE001 - when limits cannot be checked, refuse rather than run unmetered
            await send_error(
                send,
                ForgeError(ErrorCode.INTERNAL, "Rate limiting is temporarily unavailable. Retry shortly.", http_status=503, retryable=True, headers={"Retry-After": "5"}),
                request_id=rid,
            )
            return
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        now = self._monotonic()
        last = self._last_log.get(principal.client_id)
        if last is None or now - last >= _THROTTLE_LOG_INTERVAL_SECONDS:
            if len(self._last_log) > 10_000:
                self._last_log.clear()
            self._last_log[principal.client_id] = now
            await self._audit.record(
                EVENT_REQUEST_LIMITED, request_id=rid, project_id=principal.project_id, client_id=principal.client_id, token_prefix=principal.token_prefix
            )
        retry = max(1, decision.retry_after_seconds)
        await send_error(
            send,
            ForgeError(
                ErrorCode.RATE_LIMITED,
                f"Rate limit reached. Retry in {retry} seconds.",
                http_status=429,
                retryable=True,
                details={"retry_after_seconds": retry},
                headers={"Retry-After": str(retry)},
            ),
            request_id=rid,
        )
