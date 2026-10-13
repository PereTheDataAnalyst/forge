"""Harness for MCP tests: the real application on a real socket, plus raw JSON-RPC helpers.

The official MCP HTTP client needs ``httpx``, which is not always installed where these tests
run, so requests are built by hand for both protocol eras the server answers:

  * ``modern``  revision 2026-07-28: stateless, per-request ``_meta`` envelope, routing headers;
  * ``legacy``  revision 2025-11-25: plain JSON-RPC over POST (the server runs stateless).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from forge_integrations.config import McpSettings
from forge_integrations.mcp.audit import AuditRecorder, InMemoryAuditSink
from forge_integrations.mcp.deps import McpDependencies
from forge_integrations.mcp.policy import StaticPolicyProvider
from forge_integrations.mcp.ratelimit import InMemoryRateLimiter
from forge_integrations.mcp.scopes import ALL_SCOPES
from forge_integrations.mcp.server import McpHttpApp, create_mcp_app
from forge_integrations.mcp.service import IssuedToken, McpTokenService
from forge_integrations.mcp.stores import InMemoryMcpClientStore
from forge_integrations.security.tokens import TokenHasher

from .helpers import FakeAsrProvider, FakeGateway, RecordingObserver

PEPPER = "test-pepper-" + "x" * 40
MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class FakeUsage:
    """UsageReader test double returning scripted numbers, never real platform data."""

    def __init__(self, summary: Mapping[str, Any] | None = None) -> None:
        self.summary_value = dict(summary or {"requests": 12, "errors": 1, "by_source": {"mcp": 7, "api": 5}})
        self.calls: list[tuple[str, int]] = []

    async def summary(self, project_id: str, *, days: int) -> Mapping[str, Any]:
        self.calls.append((project_id, days))
        return self.summary_value


class FakeProjects:
    def __init__(self, info: Mapping[str, Any] | None = None) -> None:
        self.info = info if info is not None else {"name": "Demo project", "plan": "free"}

    async def get(self, project_id: str) -> Mapping[str, Any] | None:
        return dict(self.info) if self.info is not None else None


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    text: str

    @property
    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def result(self) -> Any:
        return self.json["result"]

    @property
    def error(self) -> Any:
        return self.json["error"]

    def tool_payload(self) -> dict[str, Any]:
        """The structured content of a tool result."""
        return self.result["structuredContent"]


def meta_envelope() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "forge-tests", "version": "1"},
    }


def rpc_request(method: str, params: Mapping[str, Any] | None = None, *, era: str = "modern", request_id: int = 1) -> tuple[bytes, dict[str, str]]:
    body_params = dict(params or {})
    headers: dict[str, str] = {}
    if era == "modern":
        body_params["_meta"] = meta_envelope()
        headers["MCP-Protocol-Version"] = MODERN_VERSION
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = str(body_params.get("name", ""))
    else:
        headers["MCP-Protocol-Version"] = LEGACY_VERSION
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
    return json.dumps(body).encode("utf-8"), headers


@contextmanager
def serve_app(app: Any) -> Iterator[tuple[Any, threading.Thread, int]]:
    """Run an ASGI application with uvicorn on a free local port, in a background thread."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on", access_log=False, server_header=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test server did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield server, thread, port
    finally:
        server.should_exit = True
        thread.join(10)


class McpHarness:
    def __init__(
        self,
        *,
        settings: McpSettings | None = None,
        gateway: Any = "default",
        asr: Any = "default",
        usage: Any = "default",
        projects: Any = None,
        rate_limiter: Any = None,
        auth_rate_limiter: Any = None,
    ) -> None:
        self.observer = RecordingObserver()
        self.sink = InMemoryAuditSink()
        self.audit = AuditRecorder(self.sink, self.observer)
        self.settings = settings or McpSettings()
        self.store = InMemoryMcpClientStore()
        self.tokens = McpTokenService(self.store, TokenHasher(PEPPER), self.settings, audit=self.audit)
        self.policy = StaticPolicyProvider()
        self.gateway = FakeGateway("scripted reply") if gateway == "default" else gateway
        self.asr = FakeAsrProvider() if asr == "default" else asr
        self.usage = FakeUsage() if usage == "default" else usage
        self.rate_limiter = rate_limiter or InMemoryRateLimiter()
        self.deps = McpDependencies(
            tokens=self.tokens,
            rate_limiter=self.rate_limiter,
            audit=self.audit,
            policy=self.policy,
            observer=self.observer,
            gateway=self.gateway,
            asr=self.asr,
            usage=self.usage,
            projects=projects,
            auth_rate_limiter=auth_rate_limiter,
        )
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    # -- tokens ---------------------------------------------------------------------------
    def issue(self, scopes: Any = None, *, project: str = "proj-1", name: str = "test agent", ttl_days: int | None = None) -> IssuedToken:
        return asyncio.run(self.tokens.issue(project_id=project, name=name, scopes=scopes, ttl_days=ttl_days))

    def issue_all(self, **kwargs: Any) -> IssuedToken:
        return self.issue(sorted(ALL_SCOPES), **kwargs)

    # -- application ----------------------------------------------------------------------
    def app(self, **kwargs: Any) -> McpHttpApp:
        return create_mcp_app(self.deps, self.settings, **kwargs)

    @contextmanager
    def serve(self, **kwargs: Any) -> Iterator["McpHarness"]:
        with serve_app(self.app(**kwargs)) as (server, thread, port):
            self._server, self._thread, self.port = server, thread, port
            try:
                yield self
            finally:
                self._server = self._thread = self.port = None

    # -- requests -------------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        token: str | None = None,
        host: str | None = None,
        timeout: float = 20.0,
    ) -> Reply:
        assert self.port is not None, "use `with harness.serve():`"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        merged = dict(headers or {})
        if token is not None:
            merged["Authorization"] = f"Bearer {token}"
        if host is not None:
            merged["Host"] = host
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            data = response.read()
            return Reply(response.status, {k.lower(): v for k, v in response.getheaders()}, data.decode("utf-8", "replace"))
        finally:
            connection.close()

    def rpc(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        token: str | None,
        era: str = "modern",
        request_id: int = 1,
        extra_headers: Mapping[str, str] | None = None,
        host: str | None = None,
        path: str = "/mcp",
    ) -> Reply:
        body, protocol_headers = rpc_request(method, params, era=era, request_id=request_id)
        headers = {**JSON_HEADERS, **protocol_headers, **(extra_headers or {})}
        return self.request("POST", path, body=body, headers=headers, token=token, host=host)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None, *, token: str | None, era: str = "modern", **kwargs: Any) -> Reply:
        return self.rpc("tools/call", {"name": name, "arguments": dict(arguments or {})}, token=token, era=era, **kwargs)

    def raw_socket(self, payload: bytes, *, read_seconds: float = 3.0, stall: float = 0.0) -> bytes:
        """Send bytes as they are and return whatever comes back. For malformed and slow clients."""
        assert self.port is not None
        with socket.create_connection(("127.0.0.1", self.port), timeout=read_seconds) as sock:
            try:
                sock.sendall(payload)
            except ConnectionError:
                pass  # the server may answer and hang up before it has read everything we sent
            if stall:
                time.sleep(stall)
            chunks: list[bytes] = []
            try:
                while True:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
            except (socket.timeout, ConnectionError):
                pass
            return b"".join(chunks)

    # -- audit ----------------------------------------------------------------------------
    def audit_events(self, event: str | None = None) -> list[dict[str, Any]]:
        events = self.sink.events
        return [e for e in events if event is None or e["event"] == event]
