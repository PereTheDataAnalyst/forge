# MCP server

Forge serves a [Model Context Protocol](https://modelcontextprotocol.io) server at `POST /mcp` on the same address as the web app. An AI assistant that speaks MCP (Claude Code, Cursor, VS Code and others) can use it to read facts about the N-ATLAS models, generate and translate text, and read usage, with a credential you create and can revoke. **There are no administrative tools.** An MCP client cannot create or delete keys, change a project, or read request content.

**Status.** The platform side is written and tested with fakes. **It has not been run against the real MCP SDK** in the environment where it was written, which had no network. Three things turn that into evidence, and they are yours to run: `scripts\probe-mcp-compat.ps1` (the SDK next to the pinned versions, plus an embedded test), `check.ps1` and, after a deploy, step 6f of `scripts\smoke-day1.ps1`, which creates a client, lists tools and calls one through `/mcp`, then revokes it and checks the key stops working. The web page for creating clients is not built yet; use the API below.

The server is the library in `integrations/src/forge_integrations/mcp`, with its own documentation in [integrations/docs/mcp.md](../integrations/docs/mcp.md): the wire format, the threat model, client configuration files for Claude Code, Cursor and VS Code (taken from vendor documentation, not run), and the tool reference. This page covers how the library is connected to the platform.

## Credentials

An MCP client is two things that must both be in force:

1. A **platform API key** with the scope `mcp.invoke` and nothing else. That key is refused by the REST gateway: it cannot call `POST /chat`, `GET /models` or `POST /audio/transcriptions`.
2. A **client row** (`mcp_clients`) that names the client and lists the tools it may use.

A key with `mcp.invoke` but no client row is refused. Both are looked up on every request and nothing is cached except the time of last use, so revoking the key or the client takes effect on the next request. Keys last 30 or 90 days (the library's own limit is 90), after which a new client is created, not renewed. A project can have 10 active clients, and they count toward its 20 active keys.

### Access

Access is granted in whole groups, because the library decides visibility by scope and several tools share one:

| Access | Tools | Notes |
| --- | --- | --- |
| `models:read` | `get_model_info` | Verified facts about the models |
| `project:read` | `get_project_info` | The project's name and the limits that apply |
| `usage:read` | `get_usage` | Counts and latency for the project. Never request content |
| `generate` | `generate`, `chat`, `translate` | Text through N-ATLAS. Counts against the project's limits |
| `transcribe` | `transcribe` | A short clip, up to 5 MiB. Hidden unless a speech worker is connected |
| `evaluate` | `evaluate` | A small developer-defined evaluation: at most 10 cases and 30 attempts |

The default is `models:read` and `project:read`. A client with a tool list that covers only part of a group gets no access from that group.

### Creating a client (API)

With a signed-in browser session and its CSRF header:

```
POST /api/v1/projects/{project_id}/mcp/clients
{ "name": "Claude Code", "access": ["models:read", "project:read", "generate"], "expires_in_days": 30 }
```

The answer has the full `key` once. Keep it in an environment variable, never in a file you commit. `GET .../mcp/clients` lists clients (status `active`, `expired` or `revoked`, last use, expiry) and never shows a key. `POST .../mcp/clients/{id}/revoke` revokes the client and its key, and may be repeated. Creating and revoking are written to the audit log.

Do not rotate an MCP client's key from the API keys page. A rotated key has no client row, so MCP refuses it. Revoke the client and create a new one.

### Using a client

The address is `https://your-forge-address/mcp`, exactly that path with no trailing slash, and the key goes in `Authorization: Bearer <key>`. Example for Claude Code, from the library's documentation:

```
claude mcp add --transport http forge https://your-forge-address/mcp --header "Authorization: Bearer $FORGE_MCP_TOKEN"
```

## How it is served

- It runs in the same process as the API. It is a plain route at exactly `/mcp`, not a mounted sub-application: a mount would redirect a bare `/mcp`, and a redirected POST breaks MCP clients.
- The MCP SDK keeps work running for the life of the process, and only the top-level application receives start-up and shutdown events. The platform starts and stops the SDK application itself.
- The MCP application is given the platform's **request ID** and **client address**. The ID in a tool result is the ID in the `X-Request-ID` header and in the Request logs page, and the address the platform worked out (it honours `TRUSTED_PROXY_COUNT`) is the one used to count failed attempts.
- Request bodies up to 8 MiB are accepted on `/mcp` (a 5 MiB clip as base64 plus the JSON around it). Every other path keeps its own limit.

### Host and Origin

The server refuses a request whose `Host` is not on its list (421) or that carries an `Origin` that is not allowed (403, and no browser origin is allowed). This stops a web page in someone's browser from driving the server. The accepted hosts are the host of `PUBLIC_BASE_URL`, the extra names in `MCP_ALLOWED_HOSTS`, and `localhost`, `127.0.0.1` and `[::1]`. If a proxy in front of the container rewrites `Host`, `/mcp` answers 421 until you add the name it sends. Command line clients send no `Origin`.

### Limits

Per client, each minute: 20 generation calls, 10 transcriptions, 3 evaluations, 60 other calls, and 300 calls per project. The counters are in Redis, shared by every process, and a Redis failure refuses calls instead of allowing them. A tool call that generates text goes through the normal gateway: it is rate limited by the project's gateway limit, and recorded in the Request logs with source `mcp` and endpoint `/mcp`. A throttled tool call is reported to the assistant straight away. It never waits.

### What is recorded

Each tool call writes an audit event with the tool name, outcome, timing and client. Prompts, replies, transcripts, audio and tokens are never in an event. Text generation is also a row in `requests`. A transcription through MCP is audited but, unlike the Voice page, does not write a `voice_requests` row, because the tool interface carries no project to attribute it to.

## When it is not running

The platform starts whether or not the MCP server can. `GET /mcp/catalog` says which, and `POST /mcp` answers 503 with a code:

| Code | Meaning |
| --- | --- |
| `mcp_disabled` | `MCP_ENABLED=false` |
| `mcp_unavailable` | The server could not start. The message names the reason: `sdk_not_installed`, `build_failed` or `startup_failed`. The log has the detail |

## Pinned versions and the probe

The production image installs the exact versions in `backend/constraints.txt`, including the SDK from the `mcp` extra. The probe in `scripts\probe-mcp-compat.ps1` reads the same file, so the two cannot disagree. The last recorded probe, on 18 October 2026, ran the SDK next to older FastAPI, Starlette and SQLAlchemy versions than the image now pins, so the current combination is unproven until the probe is run again.

## Verification

| What | Where | Run |
| --- | --- | --- |
| Access groups, authentication of platform keys (every rejection reason, revocation, last-use throttling) | `test_mcp_catalog.py`, `test_mcp_auth.py` | Without a database or the SDK |
| The `/mcp` route, lifespan bridge (including startup failure and timeout), request ID and address hand-over, host rules, graceful degradation, adapters | `test_mcp_runtime.py`, `test_mcp_adapters.py` | Without a database or the SDK |
| Client creation, limits, revocation, audit, the key being refused by the REST gateway | `test_mcp_api.py` | Needs PostgreSQL and Redis |
| A real client listing tools and calling them through the whole application | `test_mcp_api.py` (last test) | Needs PostgreSQL, Redis and the SDK |
| The real SDK through the mount, with in-memory fakes | `test_mcp_embedded.py` | Needs the SDK. The probe runs it |
| A deployment | `smoke-day1.ps1` step 6f | Against a running address |
