"""Adds defensive HTTP headers to every response."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_HSTS = "max-age=31536000"


class SecurityHeadersMiddleware:
    def __init__(
        self, app: ASGIApp, *, hsts: bool = False, docs_paths: tuple[str, ...] = ("/api/docs",)
    ) -> None:
        self.app = app
        self.hsts = hsts
        self.docs_paths = docs_paths

    def _is_docs(self, path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + "/") for prefix in self.docs_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_docs = self._is_docs(path)
        is_api = path.startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for key, value in _STATIC_HEADERS.items():
                    if key not in headers:
                        headers[key] = value
                if is_api and not is_docs and "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = _API_CSP
                if is_api and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
                if self.hsts and "strict-transport-security" not in headers:
                    headers["Strict-Transport-Security"] = _HSTS
            await send(message)

        await self.app(scope, receive, send_with_headers)
