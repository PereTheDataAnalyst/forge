"""The MCP command line: token lifecycle, configuration checks, safe start-up and the audit file."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import logging
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from forge_integrations.config import load_mcp_settings
from forge_integrations.mcp import cli
from forge_integrations.mcp.audit import AuditRecorder, JsonlAuditSink
from forge_integrations.mcp.policy import McpPolicy
from forge_integrations.mcp.service import McpTokenService
from forge_integrations.mcp.stores import SqliteMcpClientStore
from forge_integrations.security.tokens import TokenHasher, parse_token

from .helpers import RecordingObserver
from .mcp_helpers import JSON_HEADERS, rpc_request, serve_app

PEPPER = "cli-test-pepper-" + "z" * 40
POSIX = os.name == "posix"


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.store_path = self.dir / "data" / "clients.sqlite3"
        self.env = {"MCP_TOKEN_PEPPER": PEPPER, "MCP_STORE_PATH": str(self.store_path)}
        # Tests must not attach log handlers to the process; configure_logging has its own test.
        patcher = mock.patch.object(cli, "configure_logging")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        # argparse writes usage and version text to the real streams, so capture those too
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), env=self.env if env is None else env, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def issue(self, *extra: str, project: str = "proj_1", name: str = "agent") -> str:
        code, out, err = self.run_cli("issue-token", "--project", project, "--name", name, *extra)
        self.assertEqual(code, 0, err)
        return out.strip()

    def verify(self, token: str):
        """Check a token against the same store the way the server does, with the same pepper."""
        settings = load_mcp_settings(self.env)
        store = SqliteMcpClientStore(self.store_path)
        try:
            return asyncio.run(McpTokenService(store, TokenHasher(PEPPER), settings).verify(token))
        finally:
            store.close()


# --------------------------------------------------------------------------- check
class CheckTests(CliCase):
    def test_a_missing_pepper_is_reported_and_blocks(self):
        code, out, err = self.run_cli("check", env={"MCP_STORE_PATH": str(self.store_path)})
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertIn("Not ready", out)
        self.assertFalse(self.store_path.exists())  # nothing is created for a configuration that cannot run

    def test_a_short_pepper_is_a_configuration_error_that_does_not_echo_it(self):
        short = "too-short-secret"
        code, out, err = self.run_cli("check", env={"MCP_TOKEN_PEPPER": short, "MCP_STORE_PATH": str(self.store_path)})
        self.assertEqual(code, 2)
        self.assertNotIn(short, out + err)

    def test_a_good_setup_prints_a_report_without_secrets(self):
        env = dict(self.env, ASR_SERVICE_URL="http://127.0.0.1:9", ASR_SERVICE_TOKEN="asr-secret-token-1234", ALLOW_PRIVATE_NETWORK_TARGETS="true", ALLOW_INSECURE_HTTP_TARGETS="true")
        env.update(
            MCP_GATEWAY_FACTORY="tests.cli_factories:make_gateway",
            MCP_USAGE_FACTORY="tests.cli_factories:make_usage",
            MCP_PROJECTS_FACTORY="tests.cli_factories:make_projects",
            MCP_RATE_LIMITER_FACTORY="tests.cli_factories:make_limiter",
        )
        code, out, err = self.run_cli("check", env=env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ready.", out)
        self.assertIn("set (", out)
        self.assertIn("get_model_info, generate, chat, translate, transcribe, evaluate, get_usage, get_project_info", out)
        self.assertIn("ALLOW_PRIVATE_NETWORK_TARGETS is on", out)
        self.assertIn("ALLOW_INSECURE_HTTP_TARGETS is on", out)
        for secret in (PEPPER, "asr-secret-token-1234"):
            self.assertNotIn(secret, out + err)
        self.assertIn("http://127.0.0.1:9", out)  # the origin only, no path or credentials

    def test_a_bare_setup_says_which_tools_are_hidden_and_why(self):
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("generate, chat and translate are hidden", out)
        self.assertIn("the transcribe tool is hidden", out)
        self.assertIn("tools offered ....... get_model_info, get_project_info", out)
        self.assertIn("no tool can reach a model", out)

    def test_bad_factories_fail_the_check_with_the_type_only(self):
        for spec, fragment in (
            ("tests.cli_factories:broken_factory", "raised RuntimeError"),
            ("tests.cli_factories:not_callable", "is not a callable"),
            ("tests.cli_factories:missing", "is not a callable"),
            ("tests.no_such_module:thing", "could not import"),
            ("tests.cli_factories:wrong_shape", "must provide an async generate()"),
            ("os.system", "must look like package.module:function"),
            ("a:b:c", "must look like"),
            ("x; import os:y", "must look like"),
        ):
            with self.subTest(spec=spec):
                code, out, err = self.run_cli("check", env=dict(self.env, MCP_GATEWAY_FACTORY=spec))
                self.assertIn(code, (1, 2))
                self.assertIn(fragment, err)
                self.assertNotIn("hunter2", out + err)
                self.assertNotIn("db.internal", out + err)


# --------------------------------------------------------------------------- tokens
class TokenCommandTests(CliCase):
    def test_issue_prints_only_the_token_on_stdout(self):
        code, out, err = self.run_cli("issue-token", "--project", "proj_1", "--name", "Claude Code")
        self.assertEqual(code, 0, err)
        token = out.strip()
        self.assertEqual(out, token + "\n")
        parsed = parse_token(token)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.kind, "fmcp")
        self.assertIn("shown once", err)
        self.assertNotIn(token, err)
        self.assertIn("models:read, project:read", err)  # the safe default
        result = self.verify(token)
        self.assertIsNotNone(result.principal)
        self.assertEqual((result.principal.project_id, result.principal.client_name), ("proj_1", "Claude Code"))
        self.assertEqual(sorted(result.principal.scopes), ["models:read", "project:read"])

    @unittest.skipUnless(POSIX, "file modes are not enforced on Windows")
    def test_the_store_is_created_readable_by_its_owner_only(self):
        self.issue()
        self.assertEqual(self.store_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store_path.parent.stat().st_mode & 0o777, 0o700)

    def test_scopes_and_lifetime_are_honoured(self):
        token = self.issue("--scopes", "generate, usage:read", "--ttl-days", "7")
        principal = self.verify(token).principal
        self.assertEqual(sorted(principal.scopes), ["generate", "usage:read"])
        remaining = (principal.expires_at - principal.expires_at.now(principal.expires_at.tzinfo)).days
        self.assertIn(remaining, (6, 7))

    def test_json_output_has_the_token_and_never_the_hash(self):
        code, out, err = self.run_cli("issue-token", "--project", "proj_1", "--name", "x", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(parse_token(data["token"]))
        self.assertEqual(data["project_id"], "proj_1")
        self.assertNotIn("token_hash", data)
        self.assertNotIn(data["token"], err)

    def test_invalid_input_exits_2_and_prints_no_token(self):
        cases = {
            "project": ("--project", "bad id!", "--name", "x"),
            "name": ("--project", "p", "--name", "   "),
            "scopes": ("--project", "p", "--name", "x", "--scopes", "generate,bogus"),
            "ttl too long": ("--project", "p", "--name", "x", "--ttl-days", "91"),
            "ttl zero": ("--project", "p", "--name", "x", "--ttl-days", "0"),
        }
        for label, argv in cases.items():
            with self.subTest(label):
                code, out, err = self.run_cli("issue-token", *argv)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "")
                self.assertIn("Invalid input", err)

    def test_missing_arguments_are_a_usage_error(self):
        self.assertEqual(self.run_cli("issue-token", "--name", "x")[0], 2)
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("nonsense")[0], 2)

    def test_list_shows_status_and_never_secrets(self):
        first = self.issue(project="proj_1", name="first agent")
        self.issue(project="proj_1", name="second agent", )
        self.issue(project="proj_2", name="other project")
        code, out, _ = self.run_cli("list-tokens", "--project", "proj_1")
        self.assertEqual(code, 0)
        self.assertIn("first agent", out)
        self.assertIn("second agent", out)
        self.assertNotIn("other project", out)
        self.assertEqual(out.count("active"), 2)
        for needle in (first, first[-43:], "token_hash"):
            self.assertNotIn(needle, out)
        code, out, _ = self.run_cli("list-tokens", "--project", "proj_1", "--json")
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn("token_hash", row)
            self.assertNotIn("token", row)
        self.assertIn("No MCP clients", self.run_cli("list-tokens", "--project", "nobody")[1])

    def test_revoke_stops_the_token_and_is_safe_to_repeat(self):
        token = self.issue()
        client_id = json.loads(self.run_cli("list-tokens", "--project", "proj_1", "--json")[1])[0]["client_id"]
        self.assertIsNotNone(self.verify(token).principal)
        code, out, _ = self.run_cli("revoke-token", "--project", "proj_1", "--client-id", client_id)
        self.assertEqual((code, "Revoked" in out), (0, True))
        self.assertEqual(self.verify(token).reason, "revoked")
        code, out, _ = self.run_cli("revoke-token", "--project", "proj_1", "--client-id", client_id)
        self.assertEqual((code, "already revoked" in out), (0, True))
        self.assertIn("revoked", self.run_cli("list-tokens", "--project", "proj_1")[1])

    def test_revoke_cannot_reach_another_projects_client_or_an_unknown_one(self):
        token = self.issue(project="proj_1")
        client_id = json.loads(self.run_cli("list-tokens", "--project", "proj_1", "--json")[1])[0]["client_id"]
        code, out, err = self.run_cli("revoke-token", "--project", "proj_2", "--client-id", client_id)
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertIsNotNone(self.verify(token).principal)  # untouched
        code, _, err = self.run_cli("revoke-token", "--project", "proj_1", "--client-id", "no-such-client")
        self.assertEqual(code, 1)

    def test_token_events_reach_the_audit_file_without_the_token(self):
        audit = self.dir / "audit" / "mcp.jsonl"
        env = dict(self.env, MCP_AUDIT_FILE=str(audit))
        code, out, _ = self.run_cli("issue-token", "--project", "proj_1", "--name", "x", env=env)
        token = out.strip()
        client_id = json.loads(self.run_cli("list-tokens", "--project", "proj_1", "--json", env=env)[1])[0]["client_id"]
        self.run_cli("revoke-token", "--project", "proj_1", "--client-id", client_id, env=env)
        text = audit.read_text(encoding="utf-8")
        events = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([e["event"] for e in events], ["mcp.token_issued", "mcp.token_revoked"])
        self.assertEqual({e["actor"] for e in events}, {"cli"})
        self.assertNotIn(token, text)
        self.assertNotIn(token[-43:], text)

    def test_unexpected_failures_are_reported_by_type_only_unless_debugging(self):
        boom = mock.Mock(side_effect=RuntimeError("secret=abc123"))
        with mock.patch.dict(cli._COMMANDS, {"check": boom}):
            code, out, err = self.run_cli("check")
            self.assertEqual(code, 1)
            self.assertIn("RuntimeError", err)
            self.assertNotIn("secret=abc123", err)
            code, out, err = self.run_cli("check", "--debug")
            self.assertIn("Traceback", err)

    def test_version_and_help_succeed(self):
        code, out, _ = self.run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn("forge-integrations", out)
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        for command in ("serve", "issue-token", "list-tokens", "revoke-token", "check"):
            self.assertIn(command, out)

    def test_usage_errors_explain_themselves(self):
        code, out, err = self.run_cli("issue-token", "--name", "x")
        self.assertEqual(code, 2)
        self.assertIn("--project", err)


# --------------------------------------------------------------------------- serve
class ServeTests(CliCase):
    def serve_with(self, *argv: str, env: dict | None = None):
        """Run ``serve`` with the server process replaced by a recorder. Returns (code, err, calls)."""
        calls: list[tuple] = []

        def fake_run(app, options):
            calls.append((app, options))

        with mock.patch.object(cli, "run_server", fake_run):
            code, out, err = self.run_cli("serve", *argv, env=env)
        return code, err, calls

    def test_defaults_are_loopback_port_8765_and_the_mcp_path(self):
        code, err, calls = self.serve_with()
        self.assertEqual(code, 0, err)
        (_, options), = calls
        self.assertEqual((options.host, options.port, options.path), ("127.0.0.1", 8765, "/mcp"))
        self.assertEqual(options.max_concurrent_requests, 200)
        self.assertIsNone(options.ssl_certfile)

    def test_environment_and_flags_select_the_address(self):
        code, err, calls = self.serve_with(env=dict(self.env, MCP_HOST="localhost", MCP_PORT="9001", MCP_PATH="/agents/mcp"))
        self.assertEqual(code, 0, err)
        self.assertEqual((calls[0][1].host, calls[0][1].port, calls[0][1].path), ("localhost", 9001, "/agents/mcp"))
        code, err, calls = self.serve_with("--port", "9100", "--path", "/x")
        self.assertEqual((calls[0][1].port, calls[0][1].path), (9100, "/x"))

    def test_it_refuses_to_expose_tokens_in_clear_text(self):
        for host in ("0.0.0.0", "192.168.1.20", "mcp.example.com", "::"):
            with self.subTest(host=host):
                code, err, calls = self.serve_with("--host", host)
                self.assertEqual(code, 2)
                self.assertIn("without TLS", err)
                self.assertEqual(calls, [])
        code, err, calls = self.serve_with("--host", "0.0.0.0", "--behind-tls-proxy")
        self.assertEqual((code, len(calls)), (0, 1))
        self.assertIn("MCP_ALLOWED_HOSTS is not set", err)
        code, err, calls = self.serve_with("--host", "0.0.0.0", "--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem")
        self.assertEqual((code, calls[0][1].ssl_certfile, calls[0][1].ssl_keyfile), (0, "c.pem", "k.pem"))
        code, err, calls = self.serve_with("--host", "0.0.0.0", "--behind-tls-proxy", env=dict(self.env, MCP_ALLOWED_HOSTS="mcp.example.com"))
        self.assertNotIn("MCP_ALLOWED_HOSTS is not set", err)

    def test_only_one_of_certificate_and_key_is_an_error(self):
        for argv in (("--ssl-certfile", "c.pem"), ("--ssl-keyfile", "k.pem")):
            code, err, calls = self.serve_with(*argv)
            self.assertEqual((code, calls), (2, []))
            self.assertIn("both --ssl-certfile and --ssl-keyfile", err)

    def test_bad_addresses_paths_and_levels_are_refused(self):
        bad = [
            ("--port", "0"),
            ("--port", "70000"),
            ("--path", "mcp"),
            ("--path", "/"),
            ("--path", "/mcp/"),
            ("--path", "/healthz"),
            ("--path", "/readyz"),
            ("--path", "/a/../b"),
            ("--path", "/a b"),
            ("--path", "/mcp?x=1"),
        ]
        for argv in bad:
            with self.subTest(argv=argv):
                code, err, calls = self.serve_with(*argv)
                self.assertEqual((code, calls), (2, []), err)
        self.assertEqual(self.serve_with(env=dict(self.env, MCP_PORT="eighty"))[0], 2)
        self.assertEqual(self.serve_with(env=dict(self.env, MCP_LOG_LEVEL="chatty"))[0], 2)

    def test_the_pepper_is_required_to_start(self):
        code, err, calls = self.serve_with(env={"MCP_STORE_PATH": str(self.store_path)})
        self.assertEqual((code, calls), (2, []))
        self.assertIn("MCP_TOKEN_PEPPER", err)

    def test_the_assembled_server_authenticates_against_the_store_the_cli_manages(self):
        """Issue and revoke from the command line while the server is running."""
        token = self.issue("--scopes", "models:read,project:read")
        client_id = json.loads(self.run_cli("list-tokens", "--project", "proj_1", "--json")[1])[0]["client_id"]
        seen: dict = {}

        def fake_run(app, options):
            with serve_app(app) as (_server, _thread, port):
                def post(body_token):
                    body, protocol = rpc_request("tools/list", {}, era="legacy")
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                    headers = {**JSON_HEADERS, **protocol}
                    if body_token:
                        headers["Authorization"] = f"Bearer {body_token}"
                    connection.request("POST", options.path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read().decode()
                    connection.close()
                    return response.status, data

                probe = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                probe.request("GET", "/readyz")
                response = probe.getresponse()
                response.read()
                seen["ready_status"] = response.status
                probe.close()
                seen["before"] = post(token)
                seen["revoke"] = self.run_cli("revoke-token", "--project", "proj_1", "--client-id", client_id)
                seen["after"] = post(token)
                seen["anonymous"] = post(None)

        with mock.patch.object(cli, "run_server", fake_run):
            code, out, err = self.run_cli("serve", "--audit-file", str(self.dir / "audit.jsonl"))
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["ready_status"], 200)
        status, data = seen["before"]
        self.assertEqual(status, 200)
        self.assertEqual([t["name"] for t in json.loads(data)["result"]["tools"]], ["get_model_info", "get_project_info"])
        self.assertEqual(seen["revoke"][0], 0)
        self.assertEqual(seen["after"][0], 401)  # the running server noticed at once
        self.assertEqual(seen["anonymous"][0], 401)
        audit = (self.dir / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn("mcp.auth_failure", audit)
        self.assertNotIn(token, audit)

    def test_startup_event_lists_the_tools_and_hosts_but_no_secrets(self):
        observer = RecordingObserver()
        env = dict(self.env, MCP_GATEWAY_FACTORY="tests.cli_factories:make_gateway", MCP_ALLOWED_HOSTS="mcp.example.com")
        original = cli.JsonLogObserver
        with mock.patch.object(cli, "JsonLogObserver", lambda *_a, **_k: observer):
            code, err, calls = self.serve_with("--host", "0.0.0.0", "--behind-tls-proxy", env=env)
        self.assertEqual(code, 0, err)
        events = [e for e in observer.events if e.get("event") == "mcp.server_starting"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["tools"], ["get_model_info", "generate", "chat", "translate", "evaluate", "get_project_info"])
        self.assertEqual(event["allowed_hosts"], ["mcp.example.com"])
        self.assertTrue(event["behind_tls_proxy"])
        self.assertNotIn(PEPPER, json.dumps(event))
        self.assertIs(original, cli.JsonLogObserver)

    def test_store_and_audit_file_are_released_when_the_server_exits(self):
        code, err, calls = self.serve_with("--audit-file", str(self.dir / "a.jsonl"))
        self.assertEqual(code, 0, err)
        # Windows refuses to delete an open SQLite file, so a leaked connection fails here
        self.store_path.unlink()


# --------------------------------------------------------------------------- policy file
class PolicyFileTests(CliCase):
    def write(self, content, name="policy.json") -> Path:
        path = self.dir / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def get(self, provider, project):
        return asyncio.run(provider.get(project))

    def test_a_valid_policy_applies_per_project_with_a_default(self):
        path = self.write(
            {
                "default": {"allowed_scopes": ["models:read", "project:read"]},
                "projects": {"proj_off": {"enabled": False}, "proj_gen": {"allowed_scopes": ["generate"]}},
            }
        )
        provider = cli.load_policy_file(path)
        self.assertEqual(self.get(provider, "anyone"), McpPolicy(True, frozenset({"models:read", "project:read"})))
        self.assertFalse(self.get(provider, "proj_off").enabled)
        self.assertEqual(self.get(provider, "proj_gen").allowed_scopes, frozenset({"generate"}))
        self.assertEqual(self.get(provider, "proj_gen").effective(frozenset({"generate", "evaluate"})), frozenset({"generate"}))

    def test_an_empty_file_object_means_the_default_policy(self):
        provider = cli.load_policy_file(self.write("{}"))
        self.assertTrue(self.get(provider, "x").enabled)

    def test_an_empty_scope_list_allows_nothing(self):
        provider = cli.load_policy_file(self.write({"default": {"allowed_scopes": []}}))
        self.assertEqual(self.get(provider, "x").effective(frozenset({"generate"})), frozenset())

    def test_mistakes_are_errors_rather_than_silent_widening(self):
        bad = {
            "not json": "{nope",
            "not an object": "[]",
            "unknown top key": {"defualt": {}},
            "unknown policy key": {"default": {"enabeld": False}},
            "enabled not bool": {"default": {"enabled": "false"}},
            "scopes not list": {"default": {"allowed_scopes": "generate"}},
            "unknown scope": {"default": {"allowed_scopes": ["generate", "admin"]}},
            "bad project id": {"projects": {"bad id!": {}}},
            "projects not object": {"projects": []},
            "policy not object": {"projects": {"p": True}},
        }
        for label, content in bad.items():
            with self.subTest(label):
                with self.assertRaises(cli.CliError) as caught:
                    cli.load_policy_file(self.write(content))
                self.assertEqual(caught.exception.code, 2)

    def test_missing_and_oversized_files_are_refused(self):
        with self.assertRaises(cli.CliError):
            cli.load_policy_file(self.dir / "absent.json")
        big = self.write(" " * (cli.POLICY_FILE_MAX_BYTES + 1) + "{}", "big.json")
        with self.assertRaises(cli.CliError):
            cli.load_policy_file(big)

    def test_the_serve_command_loads_it_and_a_bad_file_stops_start_up(self):
        good = self.write({"projects": {"proj_1": {"enabled": False}}})
        with mock.patch.object(cli, "run_server", lambda app, options: None):
            self.assertEqual(self.run_cli("serve", "--policy-file", str(good))[0], 0)
            code, out, err = self.run_cli("serve", "--policy-file", str(self.write("{bad", "bad.json")))
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)


# --------------------------------------------------------------------------- helpers and the audit file
class LoggingTests(unittest.TestCase):
    def test_events_go_to_stderr_as_bare_json_lines_and_do_not_propagate(self):
        logger = logging.getLogger("forge.integrations")
        before = (list(logger.handlers), logger.level, logger.propagate)
        self.addCleanup(lambda: (logger.handlers.__setitem__(slice(None), before[0]), logger.setLevel(before[1]), setattr(logger, "propagate", before[2])))
        stream = io.StringIO()
        with mock.patch("sys.stderr", stream):
            cli.configure_logging("info")
            cli.configure_logging("warning")  # a second call must not add a second handler
            self.assertEqual(sum(1 for h in logger.handlers if getattr(h, "_forge_cli", False)), 1)
            logger.warning('{"event":"x"}')
            logger.info('{"event":"hidden"}')
        self.assertEqual(stream.getvalue(), '{"event":"x"}\n')
        self.assertFalse(logger.propagate)


class SmallPieceTests(unittest.TestCase):
    def test_loopback_detection(self):
        for host in ("127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.1.2.3"):
            self.assertTrue(cli.is_loopback(host), host)
        for host in ("0.0.0.0", "::", "10.0.0.1", "example.com", "", "127.0.0.1.evil.com"):
            self.assertFalse(cli.is_loopback(host), host)

    def test_origin_only_drops_credentials_paths_and_queries(self):
        self.assertEqual(cli._origin_only("https://user:pw@asr.example.com:8443/v1/audio?key=abc"), "https://asr.example.com:8443")
        self.assertEqual(cli._origin_only("http://127.0.0.1:9"), "http://127.0.0.1:9")


class JsonlAuditSinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "logs" / "audit.jsonl"

    async def test_events_are_appended_as_one_json_line_each(self):
        sink = JsonlAuditSink(self.path)
        await sink.record({"event": "a", "n": 1})
        await sink.record({"event": "b", "text": "Ẹ kú àárọ̀\nsecond line"})
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["event"] for l in lines], ["a", "b"])
        self.assertEqual(json.loads(lines[1])["text"], "Ẹ kú àárọ̀\nsecond line")  # newlines are escaped, so a line is a record

    @unittest.skipUnless(POSIX, "file modes are not enforced on Windows")
    async def test_the_file_and_a_new_directory_are_owner_only(self):
        JsonlAuditSink(self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    async def test_it_rotates_once_so_the_file_cannot_grow_without_bound(self):
        sink = JsonlAuditSink(self.path, max_bytes=1024)
        for index in range(60):
            await sink.record({"event": "x", "index": index, "pad": "p" * 40})
        rotated = self.path.with_name("audit.jsonl.1")
        self.assertTrue(rotated.exists())
        self.assertLess(self.path.stat().st_size, 1024 + 200)
        indexes = [json.loads(l)["index"] for p in (rotated, self.path) for l in p.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(indexes, sorted(indexes))  # nothing reordered
        self.assertEqual(indexes[-1], 59)

    async def test_concurrent_writers_do_not_interleave_records(self):
        sink = JsonlAuditSink(self.path)
        await asyncio.gather(*(sink.record({"event": "w", "i": i, "pad": "z" * 500}) for i in range(100)))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(json.loads(l)["i"] for l in lines), list(range(100)))

    async def test_tiny_limits_and_unusable_paths_fail_at_start_up(self):
        with self.assertRaises(ValueError):
            JsonlAuditSink(self.path, max_bytes=10)
        directory = Path(self._tmp.name) / "a-directory"
        directory.mkdir()
        with self.assertRaises(OSError):
            JsonlAuditSink(directory)

    async def test_a_failing_file_never_fails_the_caller(self):
        sink = JsonlAuditSink(self.path)
        observer = RecordingObserver()
        recorder = AuditRecorder(sink, observer)
        with mock.patch.object(sink, "_append", side_effect=OSError("disk full")):
            await recorder.record("mcp.tool_call", tool="generate")
        self.assertEqual([e["event"] for e in observer.events], ["mcp.tool_call", "mcp.audit_failure"])
        self.assertEqual(observer.events[-1]["sink_error"], "OSError")
        self.assertNotIn("disk full", json.dumps(observer.events))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
