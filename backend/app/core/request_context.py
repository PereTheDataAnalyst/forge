"""Per-request context shared by middleware, logging and services.

The context is a mutable dictionary stored in a ContextVar. Dependencies that run in a
thread pool copy the context, but they share the same dictionary object, so annotations
added anywhere during a request are visible to the access log at the end of the request.
"""

from contextvars import ContextVar, Token
from typing import Any

_context: ContextVar[dict[str, Any] | None] = ContextVar("forge_request_context", default=None)


def begin_request(request_id: str, client_ip: str | None) -> Token:
    return _context.set({"request_id": request_id, "client_ip": client_ip, "annotations": {}})


def end_request(token: Token) -> None:
    _context.reset(token)


def get_request_id() -> str | None:
    ctx = _context.get()
    return ctx["request_id"] if ctx else None


def get_client_ip() -> str | None:
    ctx = _context.get()
    return ctx["client_ip"] if ctx else None


def annotate(**fields: Any) -> None:
    """Attach structured fields to the current request for the access log."""
    ctx = _context.get()
    if ctx is not None:
        ctx["annotations"].update(fields)


def get_annotations() -> dict[str, Any]:
    ctx = _context.get()
    return dict(ctx["annotations"]) if ctx else {}
