"""Pure-ASGI helpers: headers, request ids, JSON error responses."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Iterable, MutableMapping

from ..errors import ErrorCode, ForgeError
from ..observability import new_request_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

#: Headers added to every response produced by these helpers.
BASE_RESPONSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
)


def get_header(scope: Scope, name: str) -> str | None:
    """First value of a request header (case-insensitive), decoded as latin-1."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key == wanted:
            return value.decode("latin-1")
    return None


def get_all_headers(scope: Scope, name: str) -> list[str]:
    wanted = name.lower().encode("latin-1")
    return [value.decode("latin-1") for key, value in scope.get("headers", ()) if key == wanted]


def client_address(scope: Scope) -> str | None:
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return None


def request_id_for(scope: Scope) -> str:
    """Reuse a well-formed inbound ``X-Request-ID`` or mint a new one. Stored in ``scope['state']``."""
    state = scope.setdefault("state", {})
    existing = state.get("request_id")
    if existing:
        return str(existing)
    inbound = get_header(scope, "x-request-id")
    rid = inbound if inbound and _REQUEST_ID_RE.match(inbound) else new_request_id()
    state["request_id"] = rid
    return rid


def json_body(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


async def send_json(
    send: Send,
    status: int,
    payload: Any,
    *,
    headers: Iterable[tuple[bytes, bytes]] = (),
    request_id: str | None = None,
) -> None:
    body = json_body(payload)
    response_headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        *BASE_RESPONSE_HEADERS,
        *headers,
    ]
    if request_id:
        response_headers.append((b"x-request-id", request_id.encode("ascii", "replace")))
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})


def error_headers(exc: ForgeError) -> list[tuple[bytes, bytes]]:
    return [(k.lower().encode("latin-1"), v.encode("latin-1", "replace")) for k, v in exc.headers.items()]


async def send_error(send: Send, exc: ForgeError, *, request_id: str | None = None, close: bool = False) -> None:
    headers = error_headers(exc)
    if close:
        headers.append((b"connection", b"close"))
    await send_json(send, exc.http_status, exc.to_payload(request_id), headers=headers, request_id=request_id)


def internal_error() -> ForgeError:
    return ForgeError(ErrorCode.INTERNAL, "An internal error occurred.", http_status=500)
