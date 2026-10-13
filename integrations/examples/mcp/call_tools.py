"""Call the Forge MCP server with nothing but the Python standard library.

This shows what the protocol looks like on the wire, which helps when a client misbehaves. For real
work use an MCP client (Claude Code, Cursor, VS Code, or the official SDK, see ``sdk_client.py``).

The token is read from the environment, never from a flag, so it does not end up in shell history
or in the process list:

    $env:FORGE_MCP_URL   = "http://127.0.0.1:8765/mcp"
    $env:FORGE_MCP_TOKEN = "<a token from: python -m forge_integrations.mcp issue-token ...>"

    python call_tools.py list
    python call_tools.py call get_model_info
    python call_tools.py call generate --args '{"prompt": "Say hello", "language": "en"}'

Two protocol eras are supported by the server and by this script: ``modern`` (revision 2026-07-28,
the default) and ``legacy`` (2025-11-25). The server runs statelessly, so no handshake is needed.
Exit codes: 0 success, 1 the server or the network refused, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence, TextIO

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
DEFAULT_URL = "http://127.0.0.1:8765/mcp"
MAX_REPLY_BYTES = 8 * 1024 * 1024


class CallFailed(Exception):
    """The server or the network did not give a usable answer."""


def build_request(method: str, params: Mapping[str, Any] | None, *, era: str, request_id: int = 1) -> tuple[bytes, dict[str, str]]:
    """The body and the protocol headers for one JSON-RPC request."""
    body_params: dict[str, Any] = dict(params or {})
    headers: dict[str, str] = {}
    if era == "modern":
        body_params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "forge-stdlib-example", "version": "1"},
        }
        headers["MCP-Protocol-Version"] = MODERN_VERSION
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = str(body_params.get("name", ""))
    else:
        headers["MCP-Protocol-Version"] = LEGACY_VERSION
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
    return json.dumps(message).encode("utf-8"), headers


def rpc(url: str, token: str, method: str, params: Mapping[str, Any] | None = None, *, era: str = "modern", timeout: float = 120.0) -> Any:
    """Send one request and return its ``result``. Raises ``CallFailed`` with a safe message."""
    body, protocol_headers = build_request(method, params, era=era)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            **protocol_headers,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - the operator chose this URL
            status, content_type, raw = response.status, response.headers.get("Content-Type", ""), response.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        try:
            error = json.loads(detail).get("error", {})
            detail = f"{error.get('code', 'error')}: {error.get('message', '')}".strip()
        except (ValueError, AttributeError):
            detail = detail[:200]
        raise CallFailed(f"HTTP {exc.code} {detail}".strip()) from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CallFailed(f"could not reach the server ({type(reason).__name__})") from None
    if len(raw) > MAX_REPLY_BYTES:
        raise CallFailed("the reply was larger than expected")
    if not content_type.lower().startswith("application/json"):
        raise CallFailed(f"unexpected reply type {content_type!r} (HTTP {status})")
    try:
        message = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise CallFailed("the reply was not valid JSON") from None
    if "error" in message:
        error = message["error"]
        raise CallFailed(f"JSON-RPC error {error.get('code')}: {error.get('message')}")
    return message.get("result")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call the Forge MCP server using only the standard library.")
    parser.add_argument("--era", choices=("modern", "legacy"), default="modern", help="protocol revision to speak (default modern)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("list", help="list the tools this token may use")
    call = sub.add_parser("call", help="call one tool")
    call.add_argument("tool", help="tool name, for example get_model_info")
    call.add_argument("--args", default="{}", help='tool arguments as a JSON object (default "{}")')
    return parser


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    environment = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    token = (environment.get("FORGE_MCP_TOKEN") or "").strip()
    if not token:
        print("error: set FORGE_MCP_TOKEN (create one with: python -m forge_integrations.mcp issue-token ...)", file=err)
        return 2
    url = (environment.get("FORGE_MCP_URL") or DEFAULT_URL).strip()
    try:
        if args.command == "list":
            result = rpc(url, token, "tools/list", era=args.era)
            for tool in result.get("tools", []):
                print(f"{tool['name']}\t{(tool.get('description') or '').splitlines()[0][:90]}", file=out)
            return 0
        try:
            arguments = json.loads(args.args)
        except ValueError:
            print("error: --args must be a JSON object", file=err)
            return 2
        if not isinstance(arguments, dict):
            print("error: --args must be a JSON object", file=err)
            return 2
        result = rpc(url, token, "tools/call", {"name": args.tool, "arguments": arguments}, era=args.era)
    except CallFailed as exc:
        print(f"error: {exc}", file=err)
        return 1
    print(json.dumps(result.get("structuredContent", result), ensure_ascii=False, indent=2), file=out)
    return 1 if result.get("isError") else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
