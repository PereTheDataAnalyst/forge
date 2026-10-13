"""Use the Forge MCP server from Python with the official MCP SDK client.

    pip install "mcp>=2.2,<3"

    $env:FORGE_MCP_URL   = "http://127.0.0.1:8765/mcp"
    $env:FORGE_MCP_TOKEN = "<a token from: python -m forge_integrations.mcp issue-token ...>"
    python sdk_client.py

It lists the tools the token may use, reads the verified model information, and, when the token has
the ``generate`` scope, asks the model one short question. Checked against ``mcp`` 2.2.0 with
``httpx2`` 2.13.0 in both protocol modes ("auto" and "legacy").
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx2  # installed with the mcp package
import mcp
from mcp.client.streamable_http import streamable_http_client


async def run(url: str, token: str, mode: str = "auto") -> int:
    # The Authorization header is set once on the HTTP client; it is never logged or printed here.
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
        async with mcp.Client(streamable_http_client(url, http_client=http), mode=mode) as client:
            tools = (await client.list_tools()).tools
            print("Tools this token may use:")
            for tool in tools:
                print(f"  {tool.name}")
            names = {tool.name for tool in tools}

            if "get_model_info" in names:
                info = await client.call_tool("get_model_info", {})
                llm = info.structured_content["llm"]
                print(f"\nLanguage model: {llm['repo_id']} (context {llm['context_length_tokens']} tokens)")
                print("Speech models: " + ", ".join(m["repo_id"] for m in info.structured_content["asr"]))

            if "generate" in names:
                reply = await client.call_tool("generate", {"prompt": "Greet me in one short sentence.", "language": "en", "max_tokens": 60})
                if reply.is_error:
                    error = reply.structured_content["error"]
                    print(f"\ngenerate failed: {error['code']}: {error['message']} (request {error['request_id']})")
                    return 1
                print(f"\nModel reply: {reply.structured_content['text']}")
    return 0


def main() -> int:
    token = (os.environ.get("FORGE_MCP_TOKEN") or "").strip()
    if not token:
        print("error: set FORGE_MCP_TOKEN first.", file=sys.stderr)
        return 2
    url = (os.environ.get("FORGE_MCP_URL") or "http://127.0.0.1:8765/mcp").strip()
    try:
        return asyncio.run(run(url, token))
    except Exception as exc:  # noqa: BLE001 - an example: report the type, never the headers
        print(f"error: could not complete the session ({type(exc).__name__}). Check the URL and the token.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
