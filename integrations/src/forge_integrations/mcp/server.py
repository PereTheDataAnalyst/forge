"""Forge MCP server assembly.

Two layers:

  * ``build_mcp_server`` adapts the tool box (``tools.py``) to the MCP SDK's low-level server:
    tool discovery shows each caller only what its scopes allow, and tool calls go through the
    tool box's gate. The SDK is used in stateless mode (no sessions to hijack, nothing to
    expire), answering with plain JSON.

  * ``create_mcp_app`` wraps the SDK application in the HTTP defences, in this order:

        security headers, Host and Origin allow-lists, CORS, method and content-type checks,
        concurrency cap, bearer authentication, per-token request limit, bounded body read,
        then the SDK.

Health endpoints ``/healthz`` and ``/readyz`` reveal nothing and need no token.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

from .. import __version__
from ..config import McpSettings
from ..errors import ErrorCode, ForgeError
from ..observability import new_request_id
from ..security.hostcheck import HostPolicy, OriginPolicy
from ..web.asgi import ASGIApp, Receive, Scope, Send, get_header, internal_error, request_id_for, send_error, send_json
from ..web.limits import BufferedBodyLimitMiddleware
from .auth import McpAuthMiddleware, McpRequestRateLimitMiddleware
from .deps import McpDependencies
from .principal import McpPrincipal, principal_from_request
from .tools import McpToolbox, ToolSpec

SERVER_NAME = "forge-natlas"

INSTRUCTIONS = (
    "Forge gives you scoped access to N-ATLAS language and speech models. Call get_model_info first to see "
    "what this server can do and which languages are supported: English, Hausa, Igbo and Yoruba. Your token's "
    "scopes decide which tools you can see, so a tool you cannot see needs a scope your token does not have. "
    "Every result is JSON and carries a request_id you can quote when asking for support. Inputs are validated "
    "and calls are rate limited: keep requests small, and when you see rate_limited wait the time given before "
    "retrying. Audio goes in as base64 and never as a URL. Do not put secrets in prompts."
)

PrincipalResolver = Callable[[Any], McpPrincipal | None]

_ALLOWED_HEADERS = "authorization, content-type, accept, mcp-protocol-version, mcp-method, mcp-name, x-request-id"
_EXPOSED_HEADERS = "x-request-id, retry-after, www-authenticate"


def _default_resolver(ctx: Any) -> McpPrincipal | None:
    return principal_from_request(getattr(ctx, "request", None))


def _request_id(ctx: Any) -> str:
    request = getattr(ctx, "request", None)
    scope = getattr(request, "scope", None)
    return request_id_for(scope) if isinstance(scope, dict) else new_request_id()


def _to_tool(spec: ToolSpec) -> types.Tool:
    return types.Tool(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        annotations=types.ToolAnnotations(
            title=spec.title,
            read_only_hint=spec.read_only,
            destructive_hint=False,
            idempotent_hint=spec.idempotent,
            open_world_hint=spec.open_world,
        ),
    )


def build_mcp_server(
    deps: McpDependencies,
    settings: McpSettings,
    *,
    principal_resolver: PrincipalResolver | None = None,
    toolbox: McpToolbox | None = None,
) -> Server:
    """The protocol adapter. ``principal_resolver`` exists so tests can drive the server in memory;
    over HTTP the default reads the principal the authentication middleware attached."""
    box = toolbox or McpToolbox(deps, settings)
    resolve = principal_resolver or _default_resolver

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        principal = resolve(ctx)
        if principal is None:
            return types.ListToolsResult(tools=[])
        try:
            policy = await deps.policy.get(principal.project_id)
        except Exception:  # noqa: BLE001 - a security control that cannot be read fails closed
            raise MCPError(types.INTERNAL_ERROR, "Access policy is temporarily unavailable.") from None
        effective = policy.effective(principal.scopes)
        return types.ListToolsResult(tools=[_to_tool(spec) for spec in box.visible_specs(effective)])

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        outcome = await box.call(params.name, params.arguments, resolve(ctx), _request_id(ctx))
        if outcome.unknown_tool:
            raise MCPError(types.INVALID_PARAMS, "Unknown tool.")
        text = json.dumps(outcome.payload, ensure_ascii=False, separators=(",", ":"), default=str)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=outcome.payload,
            is_error=not outcome.ok,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        title="N-ATLAS Forge",
        description="Scoped access to N-ATLAS language and speech models for agents and developer tools.",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


class McpHttpApp:
    """The HTTP application. Construct it with ``create_mcp_app``."""

    def __init__(
        self,
        deps: McpDependencies,
        settings: McpSettings,
        sdk_app: ASGIApp,
        *,
        path: str = "/mcp",
        ready_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._settings = settings
        self._sdk_app = sdk_app
        self._path = path
        self._ready_check = ready_check or deps.tokens.ping
        self._hosts = HostPolicy(settings.allowed_hosts)
        self._origins = OriginPolicy(settings.allowed_origins)
        self._active = 0

        pipeline: ASGIApp = BufferedBodyLimitMiddleware(
            sdk_app, max_bytes=settings.max_body_bytes, read_timeout_seconds=settings.body_read_timeout_seconds
        )
        pipeline = McpRequestRateLimitMiddleware(
            pipeline,
            limiter=deps.rate_limiter,
            audit=deps.audit,
            requests_per_minute=settings.rate_limit_requests_per_minute,
        )
        pipeline = McpAuthMiddleware(
            pipeline,
            tokens=deps.tokens,
            audit=deps.audit,
            limiter=deps.auth_rate_limiter or deps.rate_limiter,
            failures_per_minute=settings.auth_failures_per_minute,
        )
        self._pipeline = pipeline

    # -- ASGI entry -----------------------------------------------------------------------
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._sdk_app(scope, receive, send)  # starts and stops the SDK's own machinery
            return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        rid = request_id_for(scope)
        started = False
        cors_origin = self._origins.matches(get_header(scope, "origin"))

        async def guarded_send(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = self._with_standard_headers(message, rid, cors_origin)
            await send(message)

        try:
            path = scope.get("path", "")
            if path in ("/healthz", "/readyz"):
                await self._probe(scope, guarded_send, rid, ready=path == "/readyz")
            elif path == self._path:
                await self._handle_mcp(scope, receive, guarded_send, rid)
            else:
                await send_error(guarded_send, ForgeError(ErrorCode.NOT_FOUND, "Not found.", http_status=404), request_id=rid)
        except ForgeError as exc:
            if not started:
                await send_error(guarded_send, exc, request_id=rid)
        except Exception:  # noqa: BLE001 - never leak internals; the response, if any, has already started otherwise
            if not started:
                await send_error(guarded_send, internal_error(), request_id=rid)
            else:
                raise

    # -- pieces ---------------------------------------------------------------------------
    @staticmethod
    def _with_standard_headers(message: dict[str, Any], rid: str, cors_origin: str | None) -> dict[str, Any]:
        present = {k for k, _ in message.get("headers", [])}
        headers = list(message.get("headers", []))

        def add(name: bytes, value: str) -> None:
            if name not in present:
                headers.append((name, value.encode("latin-1")))
                present.add(name)

        add(b"cache-control", "no-store")
        add(b"x-content-type-options", "nosniff")
        add(b"referrer-policy", "no-referrer")
        add(b"x-request-id", rid)
        if cors_origin:
            add(b"access-control-allow-origin", cors_origin)
            add(b"vary", "Origin")
            add(b"access-control-expose-headers", _EXPOSED_HEADERS)
        return {**message, "headers": headers}

    async def _probe(self, scope: Scope, send: Send, rid: str, *, ready: bool) -> None:
        if scope.get("method") not in ("GET", "HEAD"):
            raise ForgeError(ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed.", http_status=405, headers={"Allow": "GET, HEAD"})
        if not ready:
            await send_json(send, 200, {"status": "ok"}, request_id=rid)
            return
        ok = False
        try:
            ok = bool(await self._ready_check())
        except Exception:  # noqa: BLE001
            ok = False
        await send_json(send, 200 if ok else 503, {"status": "ready" if ok else "not_ready"}, request_id=rid)

    async def _handle_mcp(self, scope: Scope, receive: Receive, send: Send, rid: str) -> None:
        if not self._hosts.allows(get_header(scope, "host")):
            raise ForgeError(ErrorCode.INVALID_REQUEST, "The Host header is not allowed.", http_status=421)
        origin = get_header(scope, "origin")
        if origin is not None and not self._origins.allows(origin):
            raise ForgeError(ErrorCode.FORBIDDEN, "The Origin is not allowed.", http_status=403)

        method = scope.get("method")
        if method == "OPTIONS":
            await self._preflight(scope, send, rid)
            return
        if method != "POST":
            raise ForgeError(ErrorCode.METHOD_NOT_ALLOWED, "Use POST.", http_status=405, headers={"Allow": "POST, OPTIONS"})
        media_type = (get_header(scope, "content-type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise ForgeError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Send application/json.", http_status=415)
        # Host names and media types are case-insensitive. The SDK compares them exactly, so hand it the
        # canonical forms this layer has already validated instead of turning valid requests away.
        scope["headers"] = [
            (name, value.lower() if name == b"host" else b"application/json" if name == b"content-type" else value)
            for name, value in scope["headers"]
        ]

        if self._active >= self._settings.max_concurrent_requests:
            raise ForgeError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The server is at capacity. Retry shortly.",
                http_status=503,
                retryable=True,
                headers={"Retry-After": "2"},
            )
        self._active += 1
        try:
            await self._pipeline(scope, receive, send)
        finally:
            self._active -= 1

    async def _preflight(self, scope: Scope, send: Send, rid: str) -> None:
        origin = self._origins.matches(get_header(scope, "origin"))
        if origin is None or get_header(scope, "access-control-request-method") is None:
            raise ForgeError(ErrorCode.FORBIDDEN, "Cross-origin access is not enabled for this origin.", http_status=403)
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-allow-methods", b"POST, OPTIONS"),
                    (b"access-control-allow-headers", _ALLOWED_HEADERS.encode("latin-1")),
                    (b"access-control-max-age", b"600"),
                    (b"vary", b"Origin"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})


def create_mcp_app(
    deps: McpDependencies,
    settings: McpSettings,
    *,
    path: str = "/mcp",
    ready_check: Callable[[], Awaitable[bool]] | None = None,
    principal_resolver: PrincipalResolver | None = None,
) -> McpHttpApp:
    """Build the complete, defended MCP HTTP application."""
    server = build_mcp_server(deps, settings, principal_resolver=principal_resolver)
    hosts = HostPolicy(settings.allowed_hosts)
    origins = OriginPolicy(settings.allowed_origins)
    sdk_app = server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_body_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts.sdk_patterns(),
            allowed_origins=sorted(origins.origins),
        ),
    )
    return McpHttpApp(deps, settings, sdk_app, path=path, ready_check=ready_check)
