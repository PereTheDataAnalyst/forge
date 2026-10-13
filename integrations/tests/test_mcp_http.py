"""HTTP-level tests of the MCP endpoint.

The real application runs on a real socket and is driven with hand-built JSON-RPC for both
protocol eras it answers (2026-07-28 and 2025-11-25). Pieces that need exact control of the
ASGI scope (credential stripping, forged forwarding headers, scripted body streams) are driven
directly instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
import unittest
from datetime import datetime, timezone

from forge_integrations.config import MCP_BODY_OVERHEAD_BYTES, McpSettings, base64_length
from forge_integrations.mcp.auth import McpAuthMiddleware, McpRequestRateLimitMiddleware
from forge_integrations.mcp.policy import McpPolicy
from forge_integrations.mcp.principal import STATE_KEY
from forge_integrations.mcp.ratelimit import InMemoryRateLimiter
from forge_integrations.mcp.scopes import ALL_SCOPES
from forge_integrations.mcp.service import McpTokenService
from forge_integrations.security.tokens import TokenHasher
from forge_integrations.web.limits import BufferedBodyLimitMiddleware

from .helpers import asgi_request
from .mcp_helpers import JSON_HEADERS, PEPPER, McpHarness, rpc_request

ERAS = ("modern", "legacy")
CANARY_PROMPT = "ZEBRA-CANARY-PROMPT-7731"


# --------------------------------------------------------------------------- helpers
def with_secret(token: str, secret: str = "A" * 43) -> str:
    """The same token prefix with a different secret."""
    return token[: len("fmcp_") + 8 + 1] + secret


def parse_raw(data: bytes) -> tuple[int, dict[str, list[str]], bytes]:
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    return status, headers, body


def raw_post(token: str | None, body: bytes = b"", *, extra: tuple[str, ...] = (), content_length: int | None = None, host: str = "127.0.0.1") -> bytes:
    lines = [
        "POST /mcp HTTP/1.1",
        f"Host: {host}",
        "Content-Type: application/json",
        "Accept: application/json, text/event-stream",
        "Connection: close",
    ]
    if token is not None:
        lines.append(f"Authorization: Bearer {token}")
    lines.extend(extra)
    if content_length is not None or not any(item.lower().startswith("transfer-encoding") for item in extra):
        lines.append(f"Content-Length: {len(body) if content_length is None else content_length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def normalised_error(reply) -> tuple[int, dict]:
    """Status and body with the per-request id removed, for comparing failures."""
    body = json.loads(reply.text)
    body["error"].pop("request_id", None)
    return reply.status, body


class ServedCase(unittest.TestCase):
    def start(self, settings: McpSettings | None = None, *, app_kwargs: dict | None = None, **kwargs) -> McpHarness:
        harness = McpHarness(settings=settings, **kwargs)
        stack = contextlib.ExitStack()
        stack.enter_context(harness.serve(**(app_kwargs or {})))
        self.addCleanup(stack.close)
        return harness


def list_tool_names(reply) -> list[str]:
    return [tool["name"] for tool in reply.result["tools"]]


# --------------------------------------------------------------------------- probes and basics
class ProbeTests(ServedCase):
    def test_liveness_needs_no_token_and_reveals_nothing(self):
        h = self.start()
        reply = h.request("GET", "/healthz")
        self.assertEqual((reply.status, reply.json), (200, {"status": "ok"}))

    def test_readiness_follows_the_client_store(self):
        h = self.start()
        reply = h.request("GET", "/readyz")
        self.assertEqual((reply.status, reply.json), (200, {"status": "ready"}))

    def test_readiness_is_503_when_the_check_fails_or_raises(self):
        async def not_ready() -> bool:
            return False

        async def broken() -> bool:
            raise ConnectionError("database is down at 10.0.0.5")

        for check in (not_ready, broken):
            with self.subTest(check=check.__name__):
                h = self.start(app_kwargs={"ready_check": check})
                reply = h.request("GET", "/readyz")
                self.assertEqual((reply.status, reply.json), (503, {"status": "not_ready"}))
                self.assertNotIn("10.0.0.5", reply.text)

    def test_probes_accept_only_get_and_head(self):
        h = self.start()
        for path in ("/healthz", "/readyz"):
            reply = h.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
            self.assertEqual(reply.status, 405)
            self.assertEqual(reply.headers["allow"], "GET, HEAD")
        self.assertEqual(h.request("HEAD", "/healthz").status, 200)

    def test_unknown_paths_are_json_404s(self):
        h = self.start()
        token = h.issue().token
        for path in ("/", "/mcp/", "/MCP", "/mcp/x", "/admin", "/.env", "/mcp/../healthz", "/mcp%2f", "//mcp"):
            with self.subTest(path=path):
                reply = h.request("POST", path, body=b"{}", headers=JSON_HEADERS, token=token)
                self.assertEqual(reply.status, 404)
                error = reply.json["error"]
                self.assertEqual(error["code"], "not_found")
                self.assertTrue(error["request_id"])

    def test_the_endpoint_path_is_configurable_and_the_default_is_then_gone(self):
        h = self.start(app_kwargs={"path": "/api/mcp"})
        token = h.issue_all().token
        self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy", path="/api/mcp").status, 200)
        self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy", path="/mcp").status, 404)

    def test_standard_security_headers_are_on_every_kind_of_response(self):
        h = self.start()
        good = h.issue_all().token
        replies = {
            "ok": h.rpc("tools/list", {}, token=good),
            "401": h.rpc("tools/list", {}, token=None),
            "404": h.request("GET", "/nothing"),
            "405": h.request("GET", "/mcp"),
            "415": h.request("POST", "/mcp", body=b"{}", headers={"Content-Type": "text/plain"}, token=good),
            "health": h.request("GET", "/healthz"),
        }
        for label, reply in replies.items():
            with self.subTest(label):
                self.assertEqual(reply.headers["cache-control"], "no-store")
                self.assertEqual(reply.headers["x-content-type-options"], "nosniff")
                self.assertEqual(reply.headers["referrer-policy"], "no-referrer")
                self.assertTrue(reply.headers["x-request-id"])
                self.assertNotIn("server", reply.headers)

    def test_request_id_is_reused_when_well_formed_and_replaced_otherwise(self):
        h = self.start()
        reply = h.request("GET", "/nothing", headers={"X-Request-ID": "client-supplied-1234"})
        self.assertEqual(reply.headers["x-request-id"], "client-supplied-1234")
        self.assertEqual(reply.json["error"]["request_id"], "client-supplied-1234")
        for bad in ("short", "has space in it 12345", "x" * 200, "semi;colon;12345", "uniécode-12345"):
            with self.subTest(bad=bad[:20]):
                try:
                    reply = h.request("GET", "/nothing", headers={"X-Request-ID": bad})
                except UnicodeEncodeError:
                    continue  # http.client refuses to send it at all
                self.assertNotEqual(reply.headers["x-request-id"], bad)
                self.assertTrue(reply.headers["x-request-id"].startswith("req_"))


# --------------------------------------------------------------------------- authentication
class AuthTests(ServedCase):
    def test_a_valid_token_works_in_both_eras(self):
        h = self.start()
        token = h.issue_all().token
        for era in ERAS:
            with self.subTest(era=era):
                reply = h.rpc("tools/list", {}, token=token, era=era)
                self.assertEqual(reply.status, 200, reply.text)
                self.assertEqual(len(reply.result["tools"]), 8)

    def test_the_bearer_scheme_is_case_insensitive(self):
        h = self.start()
        token = h.issue_all().token
        body, protocol = rpc_request("tools/list", {}, era="legacy")
        reply = h.request("POST", "/mcp", body=body, headers={**JSON_HEADERS, **protocol, "Authorization": f"bearer {token}"})
        self.assertEqual(reply.status, 200)

    def test_every_kind_of_failure_looks_the_same_to_the_client(self):
        h = self.start(settings=McpSettings(auth_failures_per_minute=1000))
        good = h.issue_all()
        revoked = h.issue(name="revoked one")
        asyncio.run(h.tokens.revoke("proj-1", revoked.record.client_id))
        past = McpTokenService(
            h.store, TokenHasher(PEPPER), h.settings, audit=h.audit, clock=lambda: datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        expired = asyncio.run(past.issue(project_id="proj-1", name="expired one"))
        secret = "A" * 43
        credentials = {
            "no header": None,
            "basic scheme": "Basic dXNlcjpwYXNz",
            "bearer without token": "Bearer",
            "bearer with blank token": "Bearer ",
            "token with trailing text": f"Bearer {good.token} extra",
            "oversized": "Bearer " + "A" * 500,
            "malformed": "Bearer not-a-token",
            "other kind of token": f"Bearer fk_00000000_{secret}",
            "unknown prefix": f"Bearer fmcp_00000000_{secret}",
            "wrong secret": f"Bearer {with_secret(good.token)}",
            "revoked": f"Bearer {revoked.token}",
            "expired": f"Bearer {expired.token}",
            "no scheme": good.token,
        }
        body, protocol = rpc_request("tools/list", {}, era="legacy")
        seen: dict[str, tuple[int, dict]] = {}
        challenges: dict[str, str] = {}
        for label, value in credentials.items():
            headers = {**JSON_HEADERS, **protocol}
            if value is not None:
                headers["Authorization"] = value
            reply = h.request("POST", "/mcp", body=body, headers=headers)
            seen[label] = normalised_error(reply)
            challenges[label] = reply.headers.get("www-authenticate", "")
        expected = (401, {"error": {"code": "unauthorized", "message": "A valid bearer token is required."}})
        for label, outcome in seen.items():
            self.assertEqual(outcome, expected, label)
        presented = {challenge for label, challenge in challenges.items() if label != "no header"}
        self.assertEqual(presented, {'Bearer realm="forge-mcp", error="invalid_token"'})
        self.assertEqual(challenges["no header"], 'Bearer realm="forge-mcp"')

        reasons = {event["reason"] for event in h.audit_events("mcp.auth_failure")}
        self.assertEqual(
            reasons,
            {
                "no_authorization_header",
                "bad_authorization_header",
                "malformed",
                "wrong_kind",
                "unknown_prefix",
                "bad_secret",
                "revoked",
                "expired",
            },
        )
        logged = json.dumps([h.audit_events(), h.observer.events])
        for token in (good.token, revoked.token, expired.token):
            self.assertNotIn(token, logged)
            self.assertNotIn(token[-43:], logged)

    def test_two_authorization_headers_are_refused(self):
        h = self.start()
        good = h.issue_all().token
        data = h.raw_socket(raw_post(good, b"{}", extra=(f"Authorization: Bearer {good}",)))
        status, headers, _ = parse_raw(data)
        self.assertEqual(status, 401)
        self.assertEqual({e["reason"] for e in h.audit_events("mcp.auth_failure")}, {"multiple_authorization_headers"})

    def test_a_token_in_the_query_string_is_not_a_credential(self):
        h = self.start()
        token = h.issue_all().token
        body, protocol = rpc_request("tools/list", {}, era="legacy")
        for query in (f"?access_token={token}", f"?token={token}", f"?authorization=Bearer%20{token}"):
            reply = h.request("POST", "/mcp" + query, body=body, headers={**JSON_HEADERS, **protocol})
            self.assertEqual(reply.status, 401, query)

    def test_failed_attempts_are_throttled_without_locking_out_a_valid_token(self):
        h = self.start(settings=McpSettings(auth_failures_per_minute=3))
        good = h.issue_all()
        bad = f"fmcp_00000000_{'A' * 43}"
        statuses = [h.rpc("tools/list", {}, token=bad, era="legacy").status for _ in range(5)]
        self.assertEqual(statuses, [401, 401, 401, 429, 429])
        throttled = h.rpc("tools/list", {}, token=bad, era="legacy")
        self.assertEqual(throttled.json["error"]["code"], "rate_limited")
        self.assertGreaterEqual(int(throttled.headers["retry-after"]), 1)
        # the same address, a valid credential
        for era in ERAS:
            self.assertEqual(h.rpc("tools/list", {}, token=good.token, era=era).status, 200)
        self.assertEqual(len(h.audit_events("mcp.auth_failure")), 3)
        self.assertEqual(len(h.audit_events("mcp.auth_throttled")), 1)  # one line per interval, not one per attempt

    def test_revocation_takes_effect_on_the_next_request(self):
        h = self.start()
        issued = h.issue_all()
        self.assertEqual(h.rpc("tools/list", {}, token=issued.token, era="legacy").status, 200)
        self.assertTrue(asyncio.run(h.tokens.revoke("proj-1", issued.record.client_id)))
        self.assertEqual(h.rpc("tools/list", {}, token=issued.token, era="legacy").status, 401)
        self.assertEqual(h.audit_events("mcp.auth_failure")[-1]["reason"], "revoked")

    def test_tokens_do_not_cross_projects(self):
        h = self.start()
        a = h.issue_all(project="proj-a")
        b = h.issue_all(project="proj-b")
        for token, project in ((a.token, "proj-a"), (b.token, "proj-b")):
            reply = h.call_tool("get_usage", {"days": 7}, token=token)
            self.assertEqual(reply.tool_payload()["project_id"], project)
        self.assertEqual([call[0] for call in h.usage.calls], ["proj-a", "proj-b"])
        reply = h.call_tool("get_usage", {"days": 7, "project_id": "proj-a"}, token=b.token)
        self.assertTrue(reply.result["isError"])
        self.assertEqual(reply.tool_payload()["error"]["code"], "invalid_argument")
        self.assertEqual(len(h.usage.calls), 2)


# --------------------------------------------------------------------------- Host, Origin, CORS
class HostAndOriginTests(ServedCase):
    def test_loopback_names_are_the_default_allow_list(self):
        h = self.start()
        token = h.issue_all().token
        for host in ("localhost", "localhost:8080", "127.0.0.1", f"127.0.0.1:{h.port}", "[::1]:9000", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy", host=host).status, 200)
        for host in ("evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "0.0.0.0", "10.0.0.5:80", ""):
            with self.subTest(host=host):
                reply = h.rpc("tools/list", {}, token=token, era="legacy", host=host)
                self.assertEqual(reply.status, 421, reply.text)
                self.assertEqual(reply.json["error"]["code"], "invalid_request")

    def test_configured_hosts_replace_the_defaults(self):
        h = self.start(settings=McpSettings(allowed_hosts=("mcp.example.com:443", "api.example.com", "wild.example.com:*")))
        token = h.issue_all().token
        allowed = ("mcp.example.com:443", "api.example.com", "api.example.com:8443", "wild.example.com:1234", "MCP.Example.com:443")
        refused = ("mcp.example.com", "mcp.example.com:80", "localhost", f"127.0.0.1:{h.port}", "example.com", "evil.com")
        for host in allowed:
            with self.subTest(allowed=host):
                self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy", host=host).status, 200)
        for host in refused:
            with self.subTest(refused=host):
                self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy", host=host).status, 421)

    def test_the_host_check_runs_before_authentication(self):
        h = self.start()
        reply = h.rpc("tools/list", {}, token=None, era="legacy", host="evil.example")
        self.assertEqual(reply.status, 421)
        self.assertEqual(h.audit_events("mcp.auth_failure"), [])

    def test_probes_answer_on_any_host_so_orchestrators_can_reach_them(self):
        h = self.start()
        self.assertEqual(h.request("GET", "/healthz", host="10.1.2.3:8000").status, 200)

    def test_requests_without_an_origin_are_allowed_and_unknown_origins_are_not(self):
        h = self.start(settings=McpSettings(allowed_origins=("https://app.example.com", "http://localhost:3000")))
        token = h.issue_all().token
        self.assertEqual(h.rpc("tools/list", {}, token=token, era="legacy").status, 200)
        for origin in ("https://app.example.com", "http://localhost:3000"):
            with self.subTest(allowed=origin):
                reply = h.rpc("tools/list", {}, token=token, era="legacy", extra_headers={"Origin": origin})
                self.assertEqual(reply.status, 200)
                self.assertEqual(reply.headers["access-control-allow-origin"], origin)
                self.assertEqual(reply.headers["vary"], "Origin")
                self.assertNotIn("access-control-allow-credentials", reply.headers)
        refused = (
            "https://evil.example",
            "http://app.example.com",  # scheme differs
            "https://app.example.com:8443",  # port differs
            "https://app.example.com.evil.example",
            "https://sub.app.example.com",
            "null",
            "*",
            "file://",
            "https://app.example.com/path",
        )
        for origin in refused:
            with self.subTest(refused=origin):
                reply = h.rpc("tools/list", {}, token=token, era="legacy", extra_headers={"Origin": origin})
                self.assertEqual(reply.status, 403, reply.text)
                self.assertEqual(reply.json["error"]["code"], "forbidden")
                self.assertNotIn("access-control-allow-origin", reply.headers)

    def test_with_no_origins_configured_every_browser_origin_is_refused(self):
        h = self.start()
        token = h.issue_all().token
        reply = h.rpc("tools/list", {}, token=token, era="legacy", extra_headers={"Origin": "https://app.example.com"})
        self.assertEqual(reply.status, 403)

    def test_preflight_is_answered_only_for_allowed_origins(self):
        h = self.start(settings=McpSettings(allowed_origins=("https://app.example.com",)))
        ok = h.request("OPTIONS", "/mcp", headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "POST"})
        self.assertEqual(ok.status, 204)
        self.assertEqual(ok.headers["access-control-allow-origin"], "https://app.example.com")
        self.assertEqual(ok.headers["access-control-allow-methods"], "POST, OPTIONS")
        allowed_headers = {part.strip() for part in ok.headers["access-control-allow-headers"].split(",")}
        self.assertEqual(
            allowed_headers,
            {"authorization", "content-type", "accept", "mcp-protocol-version", "mcp-method", "mcp-name", "x-request-id"},
        )
        self.assertEqual(ok.headers["access-control-max-age"], "600")
        self.assertNotIn("access-control-allow-credentials", ok.headers)
        self.assertEqual(ok.text, "")
        for headers in (
            {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
            {"Origin": "https://app.example.com"},  # not a preflight
            {"Access-Control-Request-Method": "POST"},  # no origin
        ):
            with self.subTest(headers=headers):
                self.assertEqual(h.request("OPTIONS", "/mcp", headers=headers).status, 403)

    def test_an_allowed_origin_still_has_to_authenticate(self):
        h = self.start(settings=McpSettings(allowed_origins=("https://app.example.com",)))
        reply = h.rpc("tools/list", {}, token=None, era="legacy", extra_headers={"Origin": "https://app.example.com"})
        self.assertEqual(reply.status, 401)
        self.assertEqual(reply.headers["access-control-allow-origin"], "https://app.example.com")  # so a browser can read the 401


# --------------------------------------------------------------------------- request shape and size
class RequestShapeTests(ServedCase):
    def test_only_post_and_options_are_accepted(self):
        h = self.start()
        token = h.issue_all().token
        for method in ("GET", "PUT", "DELETE", "PATCH", "HEAD"):
            with self.subTest(method=method):
                reply = h.request(method, "/mcp", token=token)
                self.assertEqual(reply.status, 405)
                self.assertEqual(reply.headers["allow"], "POST, OPTIONS")

    def test_the_content_type_must_be_json(self):
        h = self.start()
        token = h.issue_all().token
        body, protocol = rpc_request("tools/list", {}, era="legacy")
        for content_type in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "application/jsonp", "application/json-seq", "text/json"):
            with self.subTest(content_type=content_type):
                headers = {"Accept": "application/json", **protocol}
                if content_type:
                    headers["Content-Type"] = content_type
                reply = h.request("POST", "/mcp", body=body, headers=headers, token=token)
                self.assertEqual(reply.status, 415, reply.text)
        for content_type in ("application/json", "application/json; charset=utf-8", "APPLICATION/JSON;charset=UTF-8"):
            with self.subTest(content_type=content_type):
                headers = {"Accept": "application/json", "Content-Type": content_type, **protocol}
                self.assertEqual(h.request("POST", "/mcp", body=body, headers=headers, token=token).status, 200)

    def test_malformed_bodies_get_a_protocol_error_and_leak_nothing(self):
        h = self.start()
        token = h.issue_all().token
        bodies = {
            "not json": b"{not json",
            "empty": b"",
            "batch": b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]',
            "bare number": b"42",
            "null": b"null",
            "no method": b'{"jsonrpc":"2.0","id":1}',
            "deeply nested": b"[" * 200_000 + b"]" * 200_000,
            "invalid utf-8": b'{"jsonrpc":"2.0","id":1,"method":"\xff\xfe"}',
        }
        for label, body in bodies.items():
            with self.subTest(label):
                reply = h.request("POST", "/mcp", body=body, headers=JSON_HEADERS, token=token)
                self.assertIn(reply.status, (400, 422), reply.text[:200])
                parsed = reply.json
                self.assertIn(parsed["error"]["code"], (-32700, -32600, -32602))
                for needle in ("Traceback", "site-packages", "/usr/", token, "forge_integrations"):
                    self.assertNotIn(needle, reply.text)
        self.assertEqual(h.request("GET", "/healthz").status, 200)  # still serving afterwards

    def test_an_oversized_declared_body_is_refused_with_json_before_it_is_read(self):
        settings = McpSettings(max_audio_bytes=1, max_body_bytes=base64_length(1) + MCP_BODY_OVERHEAD_BYTES)
        h = self.start(settings=settings)
        token = h.issue_all().token
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"pad":"' + b"a" * (settings.max_body_bytes + 100) + b'"}}'
        reply = h.request("POST", "/mcp", body=body, headers=JSON_HEADERS, token=token)
        self.assertEqual(reply.status, 413)
        self.assertEqual(reply.json["error"]["code"], "payload_too_large")
        self.assertEqual(reply.headers.get("connection"), "close")
        self.assertEqual(h.request("GET", "/healthz").status, 200)

    def test_a_chunked_body_cannot_bypass_the_size_limit(self):
        settings = McpSettings(max_audio_bytes=1, max_body_bytes=base64_length(1) + MCP_BODY_OVERHEAD_BYTES)
        h = self.start(settings=settings)
        token = h.issue_all().token
        chunk = b"a" * 16_384
        framed = b"".join(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n" for _ in range(6)) + b"0\r\n\r\n"
        data = h.raw_socket(raw_post(token, framed, extra=("Transfer-Encoding: chunked",)), read_seconds=5)
        status, headers, body = parse_raw(data)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "payload_too_large")

    def test_a_client_that_sends_its_body_too_slowly_is_cut_off(self):
        h = self.start(settings=McpSettings(body_read_timeout_seconds=1.0))
        token = h.issue_all().token
        partial = b'{"jsonrpc":"2.0","id":1,'
        started = time.monotonic()
        data = h.raw_socket(raw_post(token, partial, content_length=400), read_seconds=8, stall=1.6)
        elapsed = time.monotonic() - started
        status, headers, body = parse_raw(data)
        self.assertEqual(status, 408)
        self.assertEqual(json.loads(body)["error"]["code"], "request_timeout")
        self.assertLess(elapsed, 6)
        self.assertEqual(h.request("GET", "/healthz").status, 200)

    def test_an_invalid_content_length_is_rejected(self):
        h = self.start()
        token = h.issue_all().token
        raw = raw_post(token, b"{}", content_length=2).replace(b"Content-Length: 2", b"Content-Length: abc")
        status, _, _ = parse_raw(h.raw_socket(raw))
        self.assertEqual(status, 400)

    def test_notifications_are_accepted_without_a_body(self):
        h = self.start()
        token = h.issue_all().token
        body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        reply = h.request("POST", "/mcp", body=body, headers=JSON_HEADERS, token=token)
        self.assertEqual((reply.status, reply.text), (202, ""))


# --------------------------------------------------------------------------- load shedding and rate limits
class LimitTests(ServedCase):
    def test_the_request_limit_is_per_token_and_covers_cheap_calls(self):
        h = self.start(settings=McpSettings(rate_limit_requests_per_minute=3))
        first, second = h.issue_all(), h.issue_all(name="second agent")
        statuses = [h.rpc("tools/list", {}, token=first.token, era="legacy").status for _ in range(5)]
        self.assertEqual(statuses, [200, 200, 200, 429, 429])
        limited = h.rpc("tools/list", {}, token=first.token, era="legacy")
        self.assertEqual(limited.json["error"]["code"], "rate_limited")
        self.assertGreaterEqual(int(limited.headers["retry-after"]), 1)
        self.assertGreaterEqual(limited.json["error"]["details"]["retry_after_seconds"], 1)
        self.assertEqual(h.rpc("tools/list", {}, token=second.token, era="legacy").status, 200)
        self.assertEqual(len(h.audit_events("mcp.request_limited")), 1)

    def test_tool_classes_have_their_own_budget_and_report_it_as_a_tool_error(self):
        h = self.start(settings=McpSettings(rate_limit_generate_per_minute=2))
        token = h.issue_all().token
        outcomes = []
        for _ in range(3):
            reply = h.call_tool("generate", {"prompt": "hello"}, token=token)
            outcomes.append((reply.status, reply.result["isError"]))
        self.assertEqual(outcomes, [(200, False), (200, False), (200, True)])
        self.assertEqual(reply.tool_payload()["error"]["code"], "rate_limited")
        # other tools are unaffected
        self.assertFalse(h.call_tool("get_model_info", {}, token=token).result["isError"])

    def test_the_server_sheds_load_instead_of_queueing_without_bound(self):
        h = self.start(settings=McpSettings(max_concurrent_requests=1))
        token = h.issue_all().token
        # A client that sends its headers and then stops holds the only slot until it goes away.
        holder = socket.create_connection(("127.0.0.1", h.port), timeout=5)
        self.addCleanup(holder.close)
        holder.sendall(raw_post(token, b'{"jsonrpc":"2.0",', content_length=400))

        def until(predicate, seconds=5.0):
            deadline = time.monotonic() + seconds
            reply = None
            while time.monotonic() < deadline:
                reply = h.rpc("tools/list", {}, token=token, era="legacy")
                if predicate(reply):
                    return reply
                time.sleep(0.05)
            return None

        shed = until(lambda reply: reply.status == 503)
        self.assertIsNotNone(shed, "the second request was never refused while the first was in flight")
        self.assertEqual(shed.json["error"]["code"], "provider_unavailable")
        self.assertTrue(shed.json["error"]["retryable"])
        self.assertEqual(shed.headers["retry-after"], "2")
        # probes are not subject to the cap, so an orchestrator can still see the process is alive
        self.assertEqual(h.request("GET", "/healthz").status, 200)
        # the slot is released as soon as the first client is gone
        holder.close()
        self.assertIsNotNone(until(lambda reply: reply.status == 200), "the slot was never released")


# --------------------------------------------------------------------------- protocol behaviour
class ProtocolTests(ServedCase):
    def test_discovery_describes_a_tools_only_server(self):
        h = self.start()
        token = h.issue().token
        reply = h.rpc("server/discover", {}, token=token, era="modern")
        self.assertEqual(reply.status, 200, reply.text)
        result = reply.result
        self.assertEqual(list(result["capabilities"]), ["tools"])
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertIn("get_model_info", result["instructions"])
        info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
        self.assertEqual((info["name"], info["title"]), ("forge-natlas", "N-ATLAS Forge"))

    def test_the_older_handshake_works_too(self):
        h = self.start()
        token = h.issue().token
        params = {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
        reply = h.rpc("initialize", params, token=token, era="legacy")
        self.assertEqual(reply.status, 200, reply.text)
        self.assertEqual(reply.result["serverInfo"]["name"], "forge-natlas")
        self.assertEqual(list(reply.result["capabilities"].keys() & {"tools", "resources", "prompts", "logging", "completions"}), ["tools"])
        self.assertEqual(h.rpc("ping", {}, token=token, era="legacy").result, {})

    def test_discovery_requires_a_token_too(self):
        h = self.start()
        self.assertEqual(h.rpc("server/discover", {}, token=None, era="modern").status, 401)

    def test_listing_follows_the_scopes_of_the_token(self):
        h = self.start()
        expectations = {
            "default read-only": (None, ["get_model_info", "get_project_info"]),
            "generate only": (["generate"], ["generate", "chat", "translate"]),
            "transcribe only": (["transcribe"], ["transcribe"]),
            "evaluate only": (["evaluate"], ["evaluate"]),
            "usage only": (["usage:read"], ["get_usage"]),
        }
        for label, (scopes, names) in expectations.items():
            token = h.issue(scopes).token
            for era in ERAS:
                with self.subTest(label=label, era=era):
                    self.assertEqual(sorted(list_tool_names(h.rpc("tools/list", {}, token=token, era=era))), sorted(names))
        everything = list_tool_names(h.rpc("tools/list", {}, token=h.issue_all().token))
        self.assertEqual(len(everything), 8)
        for name in everything:
            for forbidden in ("delete", "rotate", "shell", "exec", "admin", "infrastructure", "database", "sql", "project_create", "key"):
                self.assertNotIn(forbidden, name)

    def test_a_tool_call_returns_text_and_structured_content_in_both_eras(self):
        h = self.start()
        token = h.issue_all().token
        for era in ERAS:
            with self.subTest(era=era):
                reply = h.call_tool("get_model_info", {}, token=token, era=era)
                self.assertEqual(reply.status, 200, reply.text)
                self.assertFalse(reply.result["isError"])
                payload = reply.tool_payload()
                self.assertEqual(payload["llm"]["repo_id"], "NCAIR1/N-ATLaS")
                self.assertEqual(json.loads(reply.result["content"][0]["text"]), payload)
                self.assertEqual(payload["request_id"], reply.headers["x-request-id"])

    def test_tool_errors_are_results_and_protocol_errors_are_errors(self):
        h = self.start()
        token = h.issue_all().token
        for era in ERAS:
            with self.subTest(era=era, case="invalid arguments"):
                reply = h.call_tool("generate", {"prompt": ""}, token=token, era=era)
                self.assertTrue(reply.result["isError"])
                error = reply.tool_payload()["error"]
                self.assertEqual(error["code"], "invalid_argument")
                self.assertEqual(error["request_id"], reply.headers["x-request-id"])
            with self.subTest(era=era, case="unknown tool"):
                reply = h.call_tool("delete_project", {}, token=token, era=era)
                self.assertEqual(reply.error["code"], -32602)
                self.assertNotIn("result", reply.json)
            with self.subTest(era=era, case="unknown method"):
                reply = h.rpc("resources/list", {}, token=token, era=era)
                self.assertEqual(reply.json["error"]["code"], -32601)

    def test_a_tool_outside_the_token_scopes_is_refused_and_never_runs(self):
        h = self.start()
        token = h.issue(["models:read"]).token
        for era in ERAS:
            with self.subTest(era=era):
                reply = h.call_tool("generate", {"prompt": "hello"}, token=token, era=era)
                self.assertTrue(reply.result["isError"])
                error = reply.tool_payload()["error"]
                self.assertEqual(error["code"], "insufficient_scope")
                self.assertEqual(error["details"], {"required_scope": "generate"})
        self.assertEqual(h.gateway.requests, [])

    def test_a_project_policy_can_switch_mcp_off_or_narrow_it(self):
        h = self.start()
        token = h.issue_all().token
        h.policy.set("proj-1", McpPolicy(allowed_scopes=frozenset({"models:read"})))
        self.assertEqual(list_tool_names(h.rpc("tools/list", {}, token=token, era="legacy")), ["get_model_info"])
        self.assertEqual(h.call_tool("generate", {"prompt": "x"}, token=token).tool_payload()["error"]["code"], "insufficient_scope")
        h.policy.set("proj-1", McpPolicy(enabled=False))
        self.assertEqual(list_tool_names(h.rpc("tools/list", {}, token=token, era="legacy")), [])
        switched_off = h.call_tool("get_model_info", {}, token=token)
        self.assertTrue(switched_off.result["isError"])
        self.assertEqual(switched_off.tool_payload()["error"]["code"], "forbidden")
        self.assertEqual(len(h.audit_events("mcp.policy_denied")), 1)
        self.assertEqual(h.gateway.requests, [])
        h.policy.set("proj-1", McpPolicy())
        self.assertEqual(len(list_tool_names(h.rpc("tools/list", {}, token=token, era="legacy"))), 8)

    def test_an_unreadable_policy_fails_closed(self):
        h = self.start()
        token = h.issue_all().token

        async def broken(project_id):
            raise ConnectionError("policy table unreachable")

        h.policy.get = broken
        sdk_logger = logging.getLogger("mcp")
        previous = sdk_logger.level
        sdk_logger.setLevel(logging.CRITICAL)  # the SDK logs the failed listing with a traceback; keep test output clean
        self.addCleanup(sdk_logger.setLevel, previous)
        listing = h.rpc("tools/list", {}, token=token, era="legacy")
        self.assertNotIn("tools", json.dumps(listing.json.get("result", {})))
        self.assertNotIn("unreachable", listing.text)
        call = h.call_tool("generate", {"prompt": "x"}, token=token)
        self.assertTrue(call.json.get("error") or call.result.get("isError"))
        self.assertNotIn("unreachable", call.text)
        self.assertEqual(h.gateway.requests, [])


# --------------------------------------------------------------------------- what gets logged
class LogHygieneTests(ServedCase):
    def test_logs_and_audit_records_hold_no_prompts_outputs_audio_or_credentials(self):
        h = self.start()
        issued = h.issue_all()
        h.call_tool("generate", {"prompt": CANARY_PROMPT, "system": CANARY_PROMPT}, token=issued.token)
        h.call_tool("chat", {"messages": [{"role": "user", "content": CANARY_PROMPT}]}, token=issued.token)
        h.call_tool("generate", {"prompt": ""}, token=issued.token)
        h.call_tool("generate", {"prompt": CANARY_PROMPT, "project_id": "someone-else"}, token=issued.token)
        h.rpc("tools/list", {}, token=with_secret(issued.token))
        h.rpc("tools/list", {}, token=None)
        logged = json.dumps([h.audit_events(), h.observer.events], default=str)
        self.assertGreater(len(h.audit_events("mcp.tool_call")), 3)
        self.assertNotIn(CANARY_PROMPT, logged)
        self.assertNotIn("scripted reply", logged)  # the model output
        self.assertNotIn(issued.token, logged)
        self.assertNotIn(issued.token[-43:], logged)
        self.assertNotIn("Bearer", logged)
        self.assertNotIn(PEPPER, logged)

    def test_every_tool_call_leaves_one_audit_record_with_the_outcome(self):
        h = self.start()
        token = h.issue_all().token
        h.call_tool("get_model_info", {}, token=token)
        h.call_tool("generate", {"prompt": ""}, token=token)
        h.call_tool("nonexistent_tool", {}, token=token)
        records = h.audit_events("mcp.tool_call")
        by_tool = {(r["tool"], r["outcome"]) for r in records}
        self.assertIn(("get_model_info", "ok"), by_tool)
        self.assertIn(("generate", "invalid"), by_tool)
        for record in records:
            self.assertTrue(record["request_id"])
            self.assertEqual(record["project_id"], "proj-1")


# --------------------------------------------------------------------------- middleware, driven directly
class Recorder:
    """A downstream ASGI app that records what it was given."""

    def __init__(self) -> None:
        self.scopes: list[dict] = []
        self.bodies: list[bytes] = []
        self.messages: list[list[dict]] = []

    async def __call__(self, scope, receive, send) -> None:
        self.scopes.append(scope)
        if scope["type"] != "http":
            return
        received = []
        body = b""
        while True:
            message = await receive()
            received.append(message)
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.bodies.append(body)
        self.messages.append(received)
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}"})


class AuthMiddlewareDirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = McpHarness()
        self.limiter = InMemoryRateLimiter()
        self.inner = Recorder()
        self.mw = McpAuthMiddleware(self.inner, tokens=self.h.tokens, audit=self.h.audit, limiter=self.limiter, failures_per_minute=2)

    async def test_the_credential_is_removed_and_the_principal_attached(self):
        issued = await self.h.tokens.issue(project_id="p1", name="agent")
        response = await asgi_request(
            self.mw, "POST", "/mcp", headers={"authorization": f"Bearer {issued.token}", "x-keep": "1", "cookie": "a=b"}, body=b"{}"
        )
        self.assertEqual(response.status, 200)
        scope = self.inner.scopes[0]
        names = [name for name, _ in scope["headers"]]
        self.assertNotIn(b"authorization", names)
        self.assertEqual({b"x-keep", b"cookie", b"content-length"} - set(names), set())
        principal = scope["state"][STATE_KEY]
        self.assertEqual((principal.project_id, principal.client_name), ("p1", "agent"))
        self.assertNotIn(issued.token, repr(scope))

    async def test_other_scope_types_pass_straight_through(self):
        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            pass

        await self.mw({"type": "lifespan"}, receive, send)
        self.assertEqual(self.inner.scopes[0]["type"], "lifespan")

    async def test_failures_are_counted_per_peer_address_and_forwarding_headers_are_ignored(self):
        bad = {"authorization": f"Bearer fmcp_00000000_{'A' * 43}"}
        statuses = []
        for _ in range(3):
            statuses.append((await asgi_request(self.mw, "POST", "/mcp", headers=bad, body=b"{}", client=("203.0.113.9", 1000))).status)
        self.assertEqual(statuses, [401, 401, 429])
        # a different peer has its own budget
        other = await asgi_request(self.mw, "POST", "/mcp", headers=bad, body=b"{}", client=("203.0.113.10", 1000))
        self.assertEqual(other.status, 401)
        # a forged forwarding header neither resets the counter nor moves the blame
        forged = {**bad, "x-forwarded-for": "198.51.100.77", "forwarded": "for=198.51.100.78"}
        again = await asgi_request(self.mw, "POST", "/mcp", headers=forged, body=b"{}", client=("203.0.113.9", 1001))
        self.assertEqual(again.status, 429)
        self.assertEqual(self.inner.scopes, [])

    async def test_a_broken_limiter_does_not_turn_a_401_into_a_500(self):
        class Broken:
            async def check(self, key, limit, window):
                raise ConnectionError("redis down")

        mw = McpAuthMiddleware(self.inner, tokens=self.h.tokens, audit=self.h.audit, limiter=Broken(), failures_per_minute=2)
        response = await asgi_request(mw, "POST", "/mcp", headers={"authorization": "Bearer nope"}, body=b"{}")
        self.assertEqual(response.status, 401)

    async def test_the_request_limiter_refuses_to_run_before_authentication(self):
        mw = McpRequestRateLimitMiddleware(self.inner, limiter=self.limiter, audit=self.h.audit, requests_per_minute=5)
        with self.assertRaises(RuntimeError):
            await asgi_request(mw, "POST", "/mcp", body=b"{}")
        self.assertEqual(self.inner.scopes, [])

    async def test_a_broken_limiter_makes_the_request_limiter_refuse_rather_than_run_unmetered(self):
        class Broken:
            async def check(self, key, limit, window):
                raise ConnectionError("redis down")

        mw = McpRequestRateLimitMiddleware(self.inner, limiter=Broken(), audit=self.h.audit, requests_per_minute=5)
        issued = await self.h.tokens.issue(project_id="p1", name="agent")
        stack = McpAuthMiddleware(mw, tokens=self.h.tokens, audit=self.h.audit, limiter=self.limiter, failures_per_minute=5)
        response = await asgi_request(stack, "POST", "/mcp", headers={"authorization": f"Bearer {issued.token}"}, body=b"{}")
        self.assertEqual(response.status, 503)
        self.assertEqual(response.header("retry-after"), "5")
        self.assertEqual(self.inner.scopes, [])


class BodyLimitDirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_chunked_body_is_replayed_as_one_message(self):
        inner = Recorder()
        mw = BufferedBodyLimitMiddleware(inner, max_bytes=100)
        response = await asgi_request(mw, "POST", "/mcp", chunks=[b"ab", b"cd", b"ef"], send_content_length=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(inner.bodies, [b"abcdef"])
        self.assertEqual(len(inner.messages[0]), 1)
        self.assertFalse(inner.messages[0][0]["more_body"])

    async def test_a_body_exactly_at_the_limit_is_accepted_and_one_byte_more_is_not(self):
        inner = Recorder()
        mw = BufferedBodyLimitMiddleware(inner, max_bytes=10)
        self.assertEqual((await asgi_request(mw, "POST", "/mcp", body=b"x" * 10)).status, 200)
        over = await asgi_request(mw, "POST", "/mcp", chunks=[b"x" * 6, b"x" * 5], send_content_length=False)
        self.assertEqual(over.status, 413)
        self.assertEqual(over.header("connection"), "close")
        self.assertEqual(over.json()["error"]["code"], "payload_too_large")
        self.assertEqual(len(inner.bodies), 1)

    async def test_invalid_content_length_values_are_rejected(self):
        inner = Recorder()
        mw = BufferedBodyLimitMiddleware(inner, max_bytes=10)
        for value in ("abc", "-5", "1.5", "1 2", "0x10", ""):
            with self.subTest(value=value):
                response = await asgi_request(mw, "POST", "/mcp", headers={"content-length": value}, body=b"")
                self.assertEqual(response.status, 400)
        # non-ASCII digits (Arabic-Indic here) arrive as raw bytes that are not latin-1 text
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"content-length", "\u0661\u0662".encode("utf-8"))],
            "client": ("127.0.0.1", 1),
        }
        await mw(scope, receive, send)
        self.assertEqual(sent[0]["status"], 400)
        self.assertEqual(inner.bodies, [])

    async def test_a_stalled_body_times_out_with_408(self):
        inner = Recorder()
        mw = BufferedBodyLimitMiddleware(inner, max_bytes=100, read_timeout_seconds=0.2)
        sent: list[dict] = []

        async def receive():
            if not hasattr(receive, "given"):
                receive.given = True  # type: ignore[attr-defined]
                return {"type": "http.request", "body": b"abc", "more_body": True}
            await asyncio.sleep(30)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "client": ("127.0.0.1", 1)}
        started = time.monotonic()
        await mw(scope, receive, send)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(sent[0]["status"], 408)
        self.assertEqual(inner.bodies, [])

    async def test_a_client_that_disconnects_mid_body_is_dropped_without_running_the_app(self):
        inner = Recorder()
        mw = BufferedBodyLimitMiddleware(inner, max_bytes=100)
        queue = [{"type": "http.request", "body": b"abc", "more_body": True}, {"type": "http.disconnect"}]
        sent: list[dict] = []

        async def receive():
            return queue.pop(0)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [], "client": ("127.0.0.1", 1)}
        await mw(scope, receive, send)
        self.assertEqual((inner.scopes, sent), ([], []))

    def test_constructor_rejects_nonsense_limits(self):
        with self.assertRaises(ValueError):
            BufferedBodyLimitMiddleware(Recorder(), max_bytes=0)
        with self.assertRaises(ValueError):
            BufferedBodyLimitMiddleware(Recorder(), max_bytes=10, read_timeout_seconds=0)


class AppDirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_connections_are_closed_without_a_handshake(self):
        h = McpHarness()
        app = h.app()
        sent: list[dict] = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        await app({"type": "websocket", "path": "/mcp", "headers": [], "client": ("127.0.0.1", 1)}, receive, send)
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    async def test_an_unexpected_exception_becomes_a_generic_500_without_details(self):
        h = McpHarness()
        issued = await h.tokens.issue_all() if hasattr(h.tokens, "issue_all") else await h.tokens.issue(project_id="p", name="n", scopes=sorted(ALL_SCOPES))

        async def explode(request_id=None):
            raise RuntimeError("secret internal detail /srv/app/db.py")

        app = h.app(ready_check=explode)
        response = await asgi_request(app, "GET", "/readyz")
        self.assertEqual(response.status, 503)  # readiness failures are reported as not ready, not as a crash
        self.assertNotIn(b"secret", response.body)
        self.assertIsNotNone(issued)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
