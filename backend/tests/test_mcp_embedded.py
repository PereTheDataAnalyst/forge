"""The real MCP SDK served through the platform's /mcp mount, with no database.

This needs the MCP SDK (the `mcp` extra of forge-integrations) and is skipped without it, so the
normal `check.ps1` run skips it. `scripts/probe-mcp-compat.ps1` runs it with the SDK installed.
In-memory fakes replace the database, Redis and the model. The SDK, the library, the lifespan
bridge, the host and origin rules, the platform key authenticator and the tool box are real.

Status: written against the library's documented wire format and never run by its author, because
the SDK could not be installed where it was written. Treat the first run as evidence, not proof.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp", reason="needs the MCP SDK (forge-integrations[mcp])")
httpx = pytest.importorskip("httpx")

import app.modules.mcp.db_adapters as db_adapters  # noqa: E402
import app.modules.mcp.store as store_module  # noqa: E402
from app.modules.mcp import catalog  # noqa: E402
from app.modules.mcp.runtime import McpMount, McpRuntime  # noqa: E402
from app.security.tokens import generate_api_key, hash_api_key  # noqa: E402
from tests.conftest import make_settings  # noqa: E402
from tests.test_mcp_adapters import FakeGateway, FakeLimiter  # noqa: E402
from tests.test_mcp_auth import FakeStore, _credentials  # noqa: E402

PROTOCOL = "2026-07-28"


class FakeSink:
    def __init__(self):
        self.events = []

    def __call__(self, session_factory):
        return self

    async def record(self, event):
        self.events.append(dict(event))


class FakeUsage:
    def __init__(self, session_factory):
        pass

    async def summary(self, project_id, *, days):
        return {"window_days": days, "requests": 3, "successful": 3, "failed": 0}


class FakeProjects:
    def __init__(self, session_factory):
        pass

    async def get(self, project_id):
        return {"name": "Embedded test project", "description": None, "created_at": "2026-10-20T00:00:00+00:00"}


def _headers(token, method, name=None, extra=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
        **(extra or {}),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if name:
        headers["Mcp-Name"] = name
    return headers


def _body(method, params=None):
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "forge-embedded-test", "version": "1"},
    }
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**(params or {}), "_meta": meta}}


def test_the_sdk_serves_tools_through_the_platform_mount(monkeypatch):
    settings = make_settings(public_base_url="https://forge.example.com")
    pepper = settings.api_key_pepper.get_secret_value().encode("utf-8")
    key = generate_api_key()
    store, sink, gateway = FakeStore(), FakeSink(), FakeGateway()
    credentials = _credentials(
        key,
        key_hash=hash_api_key(key.full_key, pepper),
        client_allowed_tools=tuple(catalog.tools_for(["models:read", "project:read", "generate"])),
    )
    store.rows[key.prefix] = credentials
    monkeypatch.setattr(store_module, "DbCredentialStore", lambda session_factory: store)
    monkeypatch.setattr(db_adapters, "DbAuditSink", sink)
    monkeypatch.setattr(db_adapters, "PlatformUsageReader", FakeUsage)
    monkeypatch.setattr(db_adapters, "PlatformProjectInfo", FakeProjects)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings, session_factory=None, rate_limiter=FakeLimiter(), gateway=gateway, voice=None
        )
    )

    async def scenario():
        runtime = McpRuntime(settings)
        await runtime.start(app)
        assert runtime.state == "ready", runtime.message
        mount = McpMount(SimpleNamespace(state=SimpleNamespace(mcp=runtime)))
        transport = httpx.ASGITransport(app=mount)
        async with httpx.AsyncClient(transport=transport, base_url="https://forge.example.com") as http:

            async def call(method, params=None, *, token=key.full_key, name=None, headers=None):
                return await http.post(
                    "/mcp", json=_body(method, params), headers=_headers(token, method, name, headers)
                )

            assert (await call("tools/list", token=None)).status_code == 401
            assert (await call("tools/list", token="nfk_notarealkey_" + "x" * 43)).status_code == 401

            listed = await call("tools/list")
            assert listed.status_code == 200, listed.text
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert names == {"get_model_info", "get_project_info", "generate", "chat", "translate"}

            info = await call("tools/call", {"name": "get_model_info", "arguments": {}}, name="get_model_info")
            assert info.status_code == 200 and "N-ATLaS" in info.text
            project = await call("tools/call", {"name": "get_project_info", "arguments": {}}, name="get_project_info")
            assert project.status_code == 200 and "Embedded test project" in project.text

            said = await call(
                "tools/call",
                {"name": "generate", "arguments": {"prompt": "Say hello", "language": "en"}},
                name="generate",
            )
            assert said.status_code == 200 and "Hello." in said.text
            ((principal, payload, _, endpoint),) = gateway.calls
            assert principal.source == "mcp" and endpoint == "/mcp"
            assert principal.project_id == credentials.project_id

            # The deployment owns two host names. Anything else is refused before authentication.
            assert (await call("tools/list", headers={"Host": "evil.example.net"})).status_code == 421
            assert (await call("tools/list", headers={"Origin": "https://evil.example.net"})).status_code == 403

            # Revoking the client takes effect on the next request.
            store.rows[key.prefix] = _credentials(
                key, key_hash=credentials.key_hash, client_revoked_at=credentials.key_expires_at
            )
            assert (await call("tools/list")).status_code == 401

        await runtime.stop()
        assert runtime.http_app is None

    asyncio.run(scenario())
    # Events name the tool and the outcome. They never hold what was asked or answered.
    assert any(event.get("event", "").startswith("mcp") for event in sink.events)
    assert all("Say hello" not in str(event) and "Hello." not in str(event) for event in sink.events)
    assert all(key.full_key not in str(event) for event in sink.events)
    assert uuid.UUID(str(credentials.project_id))
