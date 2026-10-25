"""Runs the MCP server inside the platform process, at /mcp.

Why this file is more than a mount:

* Starlette's `Mount("/mcp")` only matches `/mcp/...` and redirects a bare `/mcp`, and a redirected
  POST breaks MCP clients. The endpoint is a plain route whose path is exactly `/mcp`.
* Lifespan events only run for the top-level application, but the MCP SDK keeps a task group open
  for the life of the process. `LifespanRunner` drives the SDK application's own startup and
  shutdown from the platform's startup and shutdown hooks.
* The platform and the MCP application each make a request ID and see a client address. The mount
  gives the MCP application the platform's, so the ID in a tool result is the ID in the response
  header and in the logs, and failed-login counting uses the address the platform resolved.
* The MCP SDK is imported only when the server starts. If it is missing or fails, the platform
  still runs and `/mcp` answers 503 with a clear code. A startup hook must never stop the platform.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.routing import Route

from app.core.logging import get_logger
from app.core.request_context import get_client_ip, get_request_id
from app.modules.mcp.catalog import INVOKE_SCOPE, MCP_PATH
from app.core.scopes import register_scope

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger("forge.mcp")

# Large enough for the library's default: a 5 MiB audio clip as base64, plus the JSON around it.
MCP_MAX_BODY_BYTES = 8 * 1024 * 1024
STARTUP_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")

McpState = Literal["disabled", "starting", "ready", "unavailable"]
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def allowed_hosts(settings: Settings) -> tuple[str, ...]:
    """Host header values the MCP endpoint accepts: the public host, extras, and loopback.

    Loopback is always accepted so a reverse proxy that forwards `Host: 127.0.0.1:<port>` still
    works. A request from a browser on another site cannot reach loopback names on this server.
    """
    hosts: list[str] = []
    for origin in sorted(settings.allowed_origins):
        host = urlsplit(origin).hostname
        if host:
            hosts.append(f"[{host}]" if ":" in host else host)
    hosts.extend(item.strip() for item in settings.mcp_allowed_hosts.split(",") if item.strip())
    hosts.extend(LOOPBACK_HOSTS)
    return tuple(dict.fromkeys(host.lower() for host in hosts))


class LifespanRunner:
    """Drives an ASGI application's lifespan from a background task for the life of the process."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._started: asyncio.Future[None] | None = None
        self._stopped: asyncio.Future[None] | None = None

    async def start(self, timeout: float = STARTUP_TIMEOUT_SECONDS) -> None:
        loop = asyncio.get_running_loop()
        self._started = loop.create_future()
        self._stopped = loop.create_future()
        self._task = loop.create_task(self._run())
        await self._inbox.put({"type": "lifespan.startup"})
        try:
            await asyncio.wait_for(asyncio.shield(self._started), timeout)
        except BaseException:
            await self._cancel()
            raise

    async def stop(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        if self._task is None or self._stopped is None:
            return
        await self._inbox.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(asyncio.shield(self._stopped), timeout)
        finally:
            await self._cancel()

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            # Awaiting a cancelled task raises CancelledError. The app's own failure was reported
            # through the futures, so there is nothing more to do here.
            pass

    async def _run(self) -> None:
        started, stopped = self._started, self._stopped
        assert started is not None and stopped is not None

        async def receive() -> dict[str, Any]:
            return await self._inbox.get()

        async def send(message: MutableMapping[str, Any]) -> None:
            kind = message.get("type")
            reason = str(message.get("message") or "")
            if kind == "lifespan.startup.complete":
                _resolve(started)
            elif kind == "lifespan.startup.failed":
                _fail(started, RuntimeError(reason or "startup failed"))
            elif kind == "lifespan.shutdown.complete":
                _resolve(stopped)
            elif kind == "lifespan.shutdown.failed":
                _fail(stopped, RuntimeError(reason or "shutdown failed"))

        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
        try:
            await self._app(scope, receive, send)
        except asyncio.CancelledError:
            # A cancelled future never logs "exception was never retrieved".
            started.cancel()
            raise
        except BaseException as exc:
            _fail(started, exc)
        else:
            _fail(started, RuntimeError("the application ended before it finished starting"))
        finally:
            _resolve(stopped)


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _fail(future: asyncio.Future[None], exc: BaseException) -> None:
    if not future.done():
        future.set_exception(exc)


class McpRuntime:
    """The state of the MCP server in this process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.state: McpState = "starting" if settings.mcp_enabled else "disabled"
        self.reason: str | None = None
        self.http_app: Callable[..., Awaitable[None]] | None = None
        self._runner: LifespanRunner | None = None

    @property
    def available(self) -> bool:
        return self.state == "ready" and self.http_app is not None

    @property
    def message(self) -> str:
        if self.state == "ready":
            return "The MCP server is running."
        if self.state == "disabled":
            return "The MCP server is switched off on this deployment (MCP_ENABLED=false)."
        if self.state == "starting":
            return "The MCP server is starting."
        return f"The MCP server could not start on this deployment ({self.reason or 'unknown'})."

    async def start(self, app: FastAPI) -> None:
        if not self._settings.mcp_enabled:
            self.state = "disabled"
            return
        try:
            http_app = build_http_app(app)
        except ImportError:
            # Expected on a developer machine without the extra, so only a warning. The image has it.
            self._fail("sdk_not_installed", "The MCP SDK is not installed.", level=logging.WARNING)
            return
        except Exception:
            self._fail("build_failed", "Building the MCP server failed.", exc_info=True)
            return
        runner = LifespanRunner(http_app)
        try:
            await runner.start()
        except Exception:
            self._fail("startup_failed", "The MCP server did not start.", exc_info=True)
            return
        self.http_app = http_app
        self._runner = runner
        self.state = "ready"
        self.reason = None
        log.info("mcp.ready", extra={"mcp_path": MCP_PATH})

    async def stop(self) -> None:
        runner, self._runner = self._runner, None
        self.http_app = None
        if runner is not None:
            await runner.stop()

    def _fail(
        self, reason: str, message: str, *, exc_info: bool = False, level: int = logging.ERROR
    ) -> None:
        self.state = "unavailable"
        self.reason = reason
        log.log(level, message, extra={"mcp_reason": reason}, exc_info=exc_info)


def build_http_app(app: FastAPI) -> Callable[..., Awaitable[None]]:
    """The library's MCP application wired to this platform. Imports the SDK, so it can raise."""
    from forge_integrations.config import McpSettings
    from forge_integrations.mcp.audit import AuditRecorder
    from forge_integrations.mcp.deps import McpDependencies
    from forge_integrations.mcp.policy import StaticPolicyProvider
    from forge_integrations.mcp.server import create_mcp_app

    from app.modules.mcp.adapters import (
        McpLogObserver,
        McpModelGateway,
        PlatformRateLimiter,
        SharedAsrProvider,
    )
    from app.modules.mcp.auth import PlatformTokenService
    from app.modules.mcp.db_adapters import DbAuditSink, PlatformProjectInfo, PlatformUsageReader
    from app.modules.mcp.store import DbCredentialStore

    state = app.state
    settings: Settings = state.settings
    observer = McpLogObserver()
    tokens = PlatformTokenService(
        DbCredentialStore(state.session_factory),
        settings.api_key_pepper.get_secret_value().encode("utf-8"),
    )
    voice = getattr(state, "voice", None)
    deps = McpDependencies(
        tokens=tokens,  # stands in for the library's own token service (same two methods)
        rate_limiter=PlatformRateLimiter(lambda: state.rate_limiter),
        audit=AuditRecorder(DbAuditSink(state.session_factory), observer),
        policy=StaticPolicyProvider(),
        observer=observer,
        gateway=McpModelGateway(lambda: state.gateway),
        asr=SharedAsrProvider(voice) if voice is not None and voice.configured else None,
        usage=PlatformUsageReader(state.session_factory),
        projects=PlatformProjectInfo(state.session_factory),
    )
    mcp_settings = McpSettings(
        allowed_hosts=allowed_hosts(settings),
        allowed_origins=(),
        max_body_bytes=MCP_MAX_BODY_BYTES,
    )
    return create_mcp_app(deps, mcp_settings, path=MCP_PATH, ready_check=tokens.ping)


class McpMount:
    """The ASGI endpoint at exactly /mcp. Forwards to the MCP application while it is running."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        runtime: McpRuntime | None = getattr(self._app.state, "mcp", None)
        target = runtime.http_app if runtime is not None else None
        if target is None:
            await self._unavailable(runtime, send)
            return
        # Give the MCP application the platform's request ID and client address.
        state = scope.setdefault("state", {})
        request_id = get_request_id()
        if request_id:
            state["request_id"] = request_id
        client_ip = get_client_ip()
        if client_ip:
            scope["client"] = (client_ip, 0)
        await target(scope, receive, send)

    @staticmethod
    async def _unavailable(runtime: McpRuntime | None, send: Send) -> None:
        state = runtime.state if runtime is not None else "unavailable"
        code = "mcp_disabled" if state == "disabled" else "mcp_unavailable"
        message = runtime.message if runtime is not None else "The MCP server is not available."
        body = json.dumps(
            {"error": {"code": code, "message": message, "request_id": get_request_id()}}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", b"30"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def register(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    register_scope(INVOKE_SCOPE)
    app.state.body_limit_overrides[MCP_PATH] = MCP_MAX_BODY_BYTES
    runtime = McpRuntime(settings)
    app.state.mcp = runtime

    async def _startup(application: FastAPI) -> None:
        await runtime.start(application)

    async def _shutdown(application: FastAPI) -> None:
        await runtime.stop()

    app.state.startup_hooks.append(_startup)
    app.state.shutdown_hooks.append(_shutdown)
    app.router.routes.append(Route(MCP_PATH, endpoint=McpMount(app), include_in_schema=False))
