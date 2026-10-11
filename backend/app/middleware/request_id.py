"""Assigns a request ID, resolves the client IP and opens the per-request context."""

import re
import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.net import resolve_client_ip
from app.core.request_context import begin_request, end_request

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id() -> str:
    return "req_" + secrets.token_hex(12)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp, trusted_proxy_count: int = 0) -> None:
        self.app = app
        self.trusted_proxy_count = trusted_proxy_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        inbound = headers.get("x-request-id", "")
        request_id = inbound if _VALID_REQUEST_ID.match(inbound) else new_request_id()
        peer = scope["client"][0] if scope.get("client") else None
        client_ip = resolve_client_ip(
            peer, headers.get("x-forwarded-for"), self.trusted_proxy_count
        )
        token = begin_request(request_id, client_ip)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            end_request(token)
