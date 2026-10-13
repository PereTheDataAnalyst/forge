"""The official MCP SDK HTTP client against the real server over a real socket.

This is the closest check to what Claude Code, Cursor or VS Code do: a real client library, a real
TCP connection, a Bearer token, and both protocol eras the server answers (the 2026-07-28 revision
that ``mode="auto"`` negotiates, and the 2025-11-25 handshake that ``mode="legacy"`` forces).

The client needs ``httpx2``, which comes with the ``mcp`` package. When it is missing the tests are
skipped rather than failed, so the rest of the suite still runs.
"""

from __future__ import annotations

import asyncio
import json
import unittest

try:  # the SDK client brings httpx2 with it
    import httpx2
    import mcp
    from mcp.client.streamable_http import streamable_http_client

    HAVE_CLIENT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_CLIENT = False

from forge_integrations.config import McpSettings

from .helpers import FakeGateway
from .mcp_helpers import McpHarness

EXPECTED_TOOLS = ["get_model_info", "generate", "chat", "translate", "transcribe", "evaluate", "get_usage", "get_project_info"]


def generous() -> McpSettings:
    return McpSettings(
        rate_limit_default_per_minute=100_000,
        rate_limit_generate_per_minute=100_000,
        rate_limit_transcribe_per_minute=100_000,
        rate_limit_evaluate_per_minute=100_000,
        rate_limit_project_per_minute=100_000,
        rate_limit_requests_per_minute=100_000,
    )


async def with_client(url: str, token: str, mode: str, work):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    async with http:
        async with mcp.Client(streamable_http_client(url, http_client=http), mode=mode) as client:
            return await work(client)


@unittest.skipUnless(HAVE_CLIENT, "the official MCP client (httpx2) is not installed")
class OfficialClientOverHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = McpHarness(settings=generous(), gateway=FakeGateway("Ẹ kú àárọ̀"))
        self.token = self.h.issue_all(project="proj-http").token

    def test_both_protocol_modes_list_and_call_tools_with_a_bearer_token(self):
        async def work(client):
            listing = await client.list_tools()
            info = await client.call_tool("get_model_info", {})
            reply = await client.call_tool("generate", {"prompt": "Say hello", "language": "yo"})
            invalid = await client.call_tool("generate", {"prompt": "", "extra": 1})
            project = await client.call_tool("get_project_info", {})
            return listing, info, reply, invalid, project

        with self.h.serve():
            url = f"http://127.0.0.1:{self.h.port}/mcp"
            for mode in ("auto", "legacy"):
                with self.subTest(mode=mode):
                    listing, info, reply, invalid, project = asyncio.run(with_client(url, self.token, mode, work))
                    self.assertEqual([tool.name for tool in listing.tools], EXPECTED_TOOLS)
                    self.assertFalse(info.is_error)
                    self.assertEqual(info.structured_content["llm"]["repo_id"], "NCAIR1/N-ATLaS")
                    self.assertFalse(reply.is_error)
                    self.assertEqual(reply.structured_content["text"], "Ẹ kú àárọ̀")
                    self.assertTrue(invalid.is_error)
                    self.assertEqual(invalid.structured_content["error"]["code"], "invalid_argument")
                    self.assertEqual(project.structured_content["project_id"], "proj-http")
        # Every successful generate reached the gateway once, with the caller's text unchanged.
        self.assertEqual([r.messages[-1].content for r in self.h.gateway.requests], ["Say hello", "Say hello"])

    def test_a_read_only_token_sees_only_its_tools_over_http(self):
        token = self.h.issue(["models:read"], project="proj-http").token

        async def work(client):
            return [tool.name for tool in (await client.list_tools()).tools]

        with self.h.serve():
            url = f"http://127.0.0.1:{self.h.port}/mcp"
            self.assertEqual(asyncio.run(with_client(url, token, "auto", work)), ["get_model_info"])
        self.assertEqual(self.h.gateway.requests, [])

    def test_a_wrong_or_missing_token_never_gets_a_session(self):
        async def work(client):  # pragma: no cover - must not be reached
            await client.list_tools()

        with self.h.serve():
            url = f"http://127.0.0.1:{self.h.port}/mcp"
            for label, token in (("unknown prefix", "fmcp_deadbeef_" + "x" * 43), ("not a token", "hello")):
                with self.subTest(case=label):
                    with self.assertRaises(Exception):
                        asyncio.run(with_client(url, token, "auto", work))
        self.assertEqual(self.h.gateway.requests, [])
        self.assertNotIn("x" * 43, json.dumps(self.h.audit_events(), default=str))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
