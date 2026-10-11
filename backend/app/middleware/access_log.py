"""Structured access log plus a safety net that turns unhandled exceptions into safe 500s."""

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.logging import get_logger
from app.core.request_context import get_annotations, get_client_ip

log = get_logger("forge.access")
_QUIET_PATH_PREFIXES = ("/api/v1/health/",)


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Full details go to the server log only. The client receives a generic error.
            log.exception("http.unhandled_exception")
            if response_started:
                raise
            status_code = 500
            response = error_response(
                500,
                "internal_error",
                "An unexpected error occurred. Quote the request ID when contacting support.",
            )
            await response(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope.get("path", "")
            extra = {
                "http_method": scope.get("method"),
                "http_path": path,
                "http_status": status_code,
                "duration_ms": duration_ms,
                "client_ip": get_client_ip(),
                **get_annotations(),
            }
            if status_code >= 500:
                log.error("http.request", extra=extra)
            elif path.startswith(_QUIET_PATH_PREFIXES) and status_code < 400:
                log.debug("http.request", extra=extra)
            else:
                log.info("http.request", extra=extra)
