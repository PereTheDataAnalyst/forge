"""The MCP runtime: lifespan bridge, the /mcp endpoint, host rules and graceful degradation.

These run without the web framework or the MCP SDK. Fake ASGI applications stand in for the SDK so
the platform's own code (the part that is new) is tested on its own. The real SDK is exercised in
test_mcp_embedded.py, which needs it.
"""

import asyncio
import gc
import json
import logging
from types import SimpleNamespace

from contextlib import contextmanager

from app.core.request_context import begin_request, end_request
from app.core.scopes import KNOWN_SCOPES
from app.modules.mcp import runtime as mcp_runtime
from app.modules.mcp.runtime import LifespanRunner, McpMount, McpRuntime, allowed_hosts


@contextmanager
def bind_request_context(*, request_id, client_ip):
    token = begin_request(request_id, client_ip)
    try:
        yield
    finally:
        end_request(token)


def _settings(**overrides):
    base = dict(
        mcp_enabled=True,
        mcp_allowed_hosts="",
        allowed_origins=frozenset({"https://forge.example.com"}),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- hosts ---------------------------------------------------------------------------------
def test_hosts_include_the_public_host_extras_and_loopback_without_duplicates():
    hosts = allowed_hosts(
        _settings(
            mcp_allowed_hosts=" Alt.Example.com , ,forge.example.com",
            allowed_origins=frozenset({"https://forge.example.com", "https://app.example.org:8443"}),
        )
    )
    assert "forge.example.com" in hosts and "app.example.org" in hosts
    assert "alt.example.com" in hosts
    assert {"localhost", "127.0.0.1", "[::1]"} <= set(hosts)
    assert len(hosts) == len(set(hosts))


def test_hosts_are_valid_rules_for_the_library_host_policy():
    from forge_integrations.security.hostcheck import HostPolicy

    policy = HostPolicy(allowed_hosts(_settings()))
    assert policy.allows("forge.example.com")
    assert policy.allows("forge.example.com:443")
    assert policy.allows("127.0.0.1:8000")
    assert policy.allows("localhost")
    assert not policy.allows("evil.example.net")
    assert not policy.allows("forge.example.com.evil.net")
    assert not policy.allows("")


# --- lifespan bridge ----------------------------------------------------------------------
class LifespanApp:
    """Behaves like an ASGI app that supports lifespan, and records what it was told."""

    def __init__(self, *, fail_startup=False, crash=False, finish_early=False, fail_shutdown=False):
        self.events: list[str] = []
        self.fail_startup, self.crash = fail_startup, crash
        self.finish_early, self.fail_shutdown = finish_early, fail_shutdown

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "lifespan"
        if self.crash:
            raise RuntimeError("boom")
        if self.finish_early:
            return
        message = await receive()
        assert message["type"] == "lifespan.startup"
        self.events.append("startup")
        if self.fail_startup:
            await send({"type": "lifespan.startup.failed", "message": "no port"})
            return
        await send({"type": "lifespan.startup.complete"})
        message = await receive()
        assert message["type"] == "lifespan.shutdown"
        self.events.append("shutdown")
        if self.fail_shutdown:
            await send({"type": "lifespan.shutdown.failed", "message": "stuck"})
        else:
            await send({"type": "lifespan.shutdown.complete"})


class _AsyncioLogCapture(logging.Handler):
    """asyncio reports an unretrieved future exception through its logger when it is collected."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _run_quietly(coroutine_factory):
    """Run a scenario, collect garbage, and fail if asyncio logged anything."""
    capture = _AsyncioLogCapture()
    logger = logging.getLogger("asyncio")
    logger.addHandler(capture)
    try:
        asyncio.run(coroutine_factory())
        gc.collect()
    finally:
        logger.removeHandler(capture)
    assert capture.records == [], capture.records


def test_the_bridge_starts_and_stops_the_application_in_order():
    async def scenario():
        app = LifespanApp()
        runner = LifespanRunner(app)
        await runner.start(timeout=2)
        assert app.events == ["startup"]
        await runner.stop(timeout=2)
        assert app.events == ["startup", "shutdown"]

    asyncio.run(scenario())


def test_a_failed_startup_raises_and_leaves_no_task_behind():
    async def scenario():
        runner = LifespanRunner(LifespanApp(fail_startup=True))
        try:
            await runner.start(timeout=2)
        except RuntimeError as exc:
            assert "no port" in str(exc)
        else:
            raise AssertionError("startup failure must raise")
        assert runner._task is None

    asyncio.run(scenario())


def test_an_application_that_crashes_or_ends_early_is_a_startup_failure_not_a_hang():
    def scenario_for(app):
        async def scenario():
            runner = LifespanRunner(app)
            try:
                await runner.start(timeout=2)
            except RuntimeError:
                return
            raise AssertionError("must raise")

        return scenario

    _run_quietly(scenario_for(LifespanApp(crash=True)))
    _run_quietly(scenario_for(LifespanApp(finish_early=True)))
    _run_quietly(scenario_for(LifespanApp(fail_startup=True)))


def test_a_startup_that_never_completes_times_out_and_leaves_nothing_behind():
    class Silent:
        async def __call__(self, scope, receive, send):
            await receive()
            await asyncio.sleep(30)

    async def scenario():
        runner = LifespanRunner(Silent())
        try:
            await runner.start(timeout=0.2)
        except asyncio.TimeoutError:
            assert runner._task is None
            return
        raise AssertionError("must time out")

    _run_quietly(scenario)


def test_stopping_a_runner_that_never_started_is_harmless():
    asyncio.run(LifespanRunner(LifespanApp()).stop(timeout=1))


def test_a_failing_shutdown_is_reported_but_the_task_is_cleaned_up():
    async def scenario():
        runner = LifespanRunner(LifespanApp(fail_shutdown=True))
        await runner.start(timeout=2)
        try:
            await runner.stop(timeout=2)
        except RuntimeError as exc:
            assert "stuck" in str(exc)
        assert runner._task is None

    asyncio.run(scenario())


# --- the /mcp endpoint --------------------------------------------------------------------
async def _call(mount, scope):
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await mount(scope, receive, send)
    return sent


def _app_with(runtime):
    return SimpleNamespace(state=SimpleNamespace(mcp=runtime))


def test_the_endpoint_answers_503_with_a_clear_code_until_the_server_runs():
    runtime = McpRuntime(_settings())
    runtime.state, runtime.reason = "unavailable", "sdk_not_installed"
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    sent = asyncio.run(_call(McpMount(_app_with(runtime)), scope))
    assert sent[0]["status"] == 503
    headers = dict(sent[0]["headers"])
    assert headers[b"content-type"] == b"application/json" and headers[b"retry-after"] == b"30"
    body = json.loads(sent[1]["body"])
    assert body["error"]["code"] == "mcp_unavailable"
    assert "sdk_not_installed" in body["error"]["message"]
    assert int(headers[b"content-length"]) == len(sent[1]["body"])


def test_a_disabled_server_says_so():
    runtime = McpRuntime(_settings(mcp_enabled=False))
    sent = asyncio.run(
        _call(McpMount(_app_with(runtime)), {"type": "http", "method": "POST", "path": "/mcp"})
    )
    assert json.loads(sent[1]["body"])["error"]["code"] == "mcp_disabled"


def test_a_missing_runtime_still_answers_503_instead_of_raising():
    sent = asyncio.run(
        _call(McpMount(SimpleNamespace(state=SimpleNamespace())), {"type": "http", "path": "/mcp"})
    )
    assert sent[0]["status"] == 503


def test_the_mount_gives_the_mcp_application_the_platform_request_id_and_client_address():
    seen = {}

    async def mcp_app(scope, receive, send):
        seen["state"] = dict(scope["state"])
        seen["client"] = scope["client"]
        seen["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    runtime = McpRuntime(_settings())
    runtime.state, runtime.http_app = "ready", mcp_app
    scope = {"type": "http", "method": "POST", "path": "/mcp", "client": ("10.0.0.1", 5555)}
    with bind_request_context(request_id="req_abc123def456", client_ip="203.0.113.9"):
        sent = asyncio.run(_call(McpMount(_app_with(runtime)), scope))
    assert sent[0]["status"] == 200
    assert seen["state"]["request_id"] == "req_abc123def456"
    assert seen["client"] == ("203.0.113.9", 0)
    assert seen["path"] == "/mcp"


def test_without_platform_context_the_scope_is_left_alone():
    seen = {}

    async def mcp_app(scope, receive, send):
        seen["client"] = scope["client"]
        seen["has_request_id"] = "request_id" in scope.get("state", {})

    runtime = McpRuntime(_settings())
    runtime.state, runtime.http_app = "ready", mcp_app
    scope = {"type": "http", "method": "POST", "path": "/mcp", "client": ("10.0.0.1", 5555)}
    asyncio.run(_call(McpMount(_app_with(runtime)), scope))
    assert seen == {"client": ("10.0.0.1", 5555), "has_request_id": False}


def test_non_http_scopes_are_ignored():
    sent = asyncio.run(_call(McpMount(_app_with(McpRuntime(_settings()))), {"type": "websocket"}))
    assert sent == []


# --- runtime state ------------------------------------------------------------------------
def _start(runtime, build):
    original = mcp_runtime.build_http_app
    mcp_runtime.build_http_app = build
    try:
        asyncio.run(runtime.start(SimpleNamespace(state=SimpleNamespace())))
    finally:
        mcp_runtime.build_http_app = original


def test_a_missing_sdk_degrades_to_unavailable_and_never_raises():
    def build(app):
        raise ImportError("No module named 'mcp'")

    runtime = McpRuntime(_settings())
    _start(runtime, build)
    assert runtime.state == "unavailable" and runtime.reason == "sdk_not_installed"
    assert not runtime.available and "sdk_not_installed" in runtime.message


def test_any_build_error_degrades_to_unavailable_and_never_raises():
    def build(app):
        raise ValueError("bad settings")

    runtime = McpRuntime(_settings())
    _start(runtime, build)
    assert runtime.state == "unavailable" and runtime.reason == "build_failed"


def test_a_failed_application_startup_degrades_to_unavailable():
    runtime = McpRuntime(_settings())
    _start(runtime, lambda app: LifespanApp(fail_startup=True))
    assert runtime.state == "unavailable" and runtime.reason == "startup_failed"
    assert runtime.http_app is None


def test_a_running_server_is_ready_and_stops_cleanly():
    app = LifespanApp()
    runtime = McpRuntime(_settings())

    async def scenario():
        original = mcp_runtime.build_http_app
        mcp_runtime.build_http_app = lambda application: app
        try:
            await runtime.start(SimpleNamespace(state=SimpleNamespace()))
        finally:
            mcp_runtime.build_http_app = original
        assert runtime.available and runtime.state == "ready"
        await runtime.stop()

    asyncio.run(scenario())
    assert app.events == ["startup", "shutdown"]
    assert runtime.http_app is None


def test_a_disabled_server_never_builds_anything():
    def build(app):
        raise AssertionError("must not be built")

    runtime = McpRuntime(_settings(mcp_enabled=False))
    _start(runtime, build)
    assert runtime.state == "disabled" and not runtime.available


# --- registration -------------------------------------------------------------------------
def test_register_adds_the_scope_the_body_limit_the_hooks_and_an_exact_path_route():
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=_settings(),
            body_limit_overrides={},
            startup_hooks=[],
            shutdown_hooks=[],
        ),
        router=SimpleNamespace(routes=[]),
    )
    mcp_runtime.register(app)
    assert "mcp.invoke" in KNOWN_SCOPES
    assert app.state.body_limit_overrides["/mcp"] >= 8 * 1024 * 1024
    assert len(app.state.startup_hooks) == 1 and len(app.state.shutdown_hooks) == 1
    assert isinstance(app.state.mcp, McpRuntime)
    (route,) = app.router.routes
    assert route.path == "/mcp"  # a plain route, not a Mount: a bare /mcp must match
    assert isinstance(route.endpoint, McpMount)
    assert route.options.get("include_in_schema") is False


def test_the_startup_hook_never_raises_even_when_the_build_fails():
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=_settings(),
            body_limit_overrides={},
            startup_hooks=[],
            shutdown_hooks=[],
        ),
        router=SimpleNamespace(routes=[]),
    )
    mcp_runtime.register(app)

    def build(application):
        raise ImportError("no sdk")

    original = mcp_runtime.build_http_app
    mcp_runtime.build_http_app = build
    try:
        asyncio.run(app.state.startup_hooks[0](app))
        asyncio.run(app.state.shutdown_hooks[0](app))
    finally:
        mcp_runtime.build_http_app = original
    assert app.state.mcp.state == "unavailable"
