"""Rejects oversized request bodies, both by Content-Length and while streaming."""

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response


class BodySizeLimitMiddleware:
    """Enforces a maximum request body size.

    `overrides` maps a path prefix to a larger or smaller limit (for example an audio upload
    route). The dictionary is read on every request, so extension modules may add entries
    during their startup hook.
    """

    def __init__(
        self, app: ASGIApp, default_limit: int, overrides: dict[str, int] | None = None
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.overrides: dict[str, int] = overrides if overrides is not None else {}

    def _limit_for(self, path: str) -> int:
        limit = self.default_limit
        longest = -1
        for prefix, value in self.overrides.items():
            if path.startswith(prefix) and len(prefix) > longest:
                limit, longest = value, len(prefix)
        return limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))

        declared: int | None = None
        for key, value in scope["headers"]:
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                break
        if declared is not None and (declared < 0 or declared > limit):
            if declared < 0:
                response = error_response(400, "bad_request", "The Content-Length header is invalid.")
            else:
                response = error_response(
                    413, "payload_too_large", f"The request body exceeds the {limit} byte limit."
                )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException unchanged, so this becomes a clean 413.
                    raise HTTPException(status_code=413, detail="Payload Too Large")
            return message

        await self.app(scope, limited_receive, send)
