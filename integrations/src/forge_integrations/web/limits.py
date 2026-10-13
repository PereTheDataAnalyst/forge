"""Request body size limit as pure ASGI middleware.

Two layers: a declared ``Content-Length`` above the limit is rejected before the body is
read, and the bytes of every request are counted as they stream in so chunked uploads
without a length cannot bypass the limit.
"""

from __future__ import annotations

import asyncio

from ..errors import ErrorCode, ForgeError
from .asgi import ASGIApp, Receive, Scope, Send, get_header, request_id_for, send_error


def _too_large(max_bytes: int) -> ForgeError:
    return ForgeError(
        ErrorCode.PAYLOAD_TOO_LARGE,
        f"The request body exceeds the {max_bytes} byte limit.",
        http_status=413,
    )


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = request_id_for(scope)
        declared = get_header(scope, "content-length")
        if declared is not None:
            if not declared.isascii() or not declared.isdigit():
                await send_error(
                    send,
                    ForgeError(ErrorCode.INVALID_REQUEST, "The Content-Length header is invalid.", http_status=400),
                    request_id=rid,
                    close=True,
                )
                return
            if int(declared) > self.max_bytes:
                await send_error(send, _too_large(self.max_bytes), request_id=rid, close=True)
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _too_large(self.max_bytes)
            return message

        async def tracking_send(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except ForgeError as exc:
            # Raised from ``receive`` and not handled by the application.
            if response_started:
                raise
            await send_error(send, exc, request_id=rid, close=True)


class BufferedBodyLimitMiddleware:
    """Read the whole request body, bounded in size and in time, before the application sees it.

    For small JSON requests. Unlike ``BodySizeLimitMiddleware`` it answers an oversized or stalled
    body itself, with a JSON error, before the application has started a response, so the outcome
    is the same for declared lengths and for chunked uploads. A client that sends its body too
    slowly (a slow-loris) is cut off with 408 once ``read_timeout_seconds`` has passed. A client that
    disconnects before finishing is dropped without running the application.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, read_timeout_seconds: float = 30.0) -> None:
        if max_bytes < 1 or read_timeout_seconds <= 0:
            raise ValueError("max_bytes and read_timeout_seconds must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.read_timeout = read_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = request_id_for(scope)
        declared = get_header(scope, "content-length")
        if declared is not None:
            if not declared.isascii() or not declared.isdigit():
                await send_error(
                    send,
                    ForgeError(ErrorCode.INVALID_REQUEST, "The Content-Length header is invalid.", http_status=400),
                    request_id=rid,
                    close=True,
                )
                return
            if int(declared) > self.max_bytes:
                await send_error(send, _too_large(self.max_bytes), request_id=rid, close=True)
                return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.read_timeout
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                await self._timed_out(send, rid)
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                await self._timed_out(send, rid)
                return
            if message["type"] != "http.request":
                # The client went away before it finished sending. Nobody is left to answer, and
                # running the application on half a request would only log a spurious error.
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await send_error(send, _too_large(self.max_bytes), request_id=rid, close=True)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replay: list[dict] = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

        async def replaying_receive():
            if replay:
                return replay.pop(0)
            return await receive()

        await self.app(scope, replaying_receive, send)

    @staticmethod
    async def _timed_out(send: Send, rid: str) -> None:
        await send_error(
            send,
            ForgeError(ErrorCode.REQUEST_TIMEOUT, "The request body was not received in time.", http_status=408),
            request_id=rid,
            close=True,
        )
