# Forge MCP server

The Forge MCP server lets an AI coding assistant (Claude Code, Cursor, VS Code, or any MCP client) use a
project's N-ATLAS access through a small set of safe tools. It is read-mostly by design: nothing in it can
create or delete projects, rotate keys, change settings, touch infrastructure, a database or a shell.

Protocol: MCP Streamable HTTP, answered statelessly in JSON. It speaks both revisions that clients use today:
`2026-07-28` (the SDK's modern mode) and `2025-11-25` (the initialize handshake). Both were exercised
against the official `mcp` 2.2.0 client over a real socket (`tests/test_mcp_official_http_client.py`).

## Tools

| Tool | Scope needed | Does | Rate class |
| --- | --- | --- | --- |
| `get_model_info` | `models:read` | Verified N-ATLAS model information (see `n-atlas-verified-facts.md`). No model call. | default |
| `generate` | `generate` | One answer from the language model. | generate |
| `chat` | `generate` | A short conversation turn. Only the first message may be `system`; the last must be `user`. | generate |
| `translate` | `generate` | Translate between `en`, `ha`, `ig`, `yo`. The result carries a notice that quality is not measured. | generate |
| `transcribe` | `transcribe` | Speech to text with the official model for the stated language. Audio is sent as base64. | transcribe |
| `evaluate` | `evaluate` | Run a small developer-written dataset (see `evaluation.md`). | evaluate |
| `get_usage` | `usage:read` | Aggregate usage for the token's own project, 1 to 30 days. | default |
| `get_project_info` | `project:read` | The token's own client, effective scopes, expiry, rate limits and visible tools, plus project details when the platform provides them. Never the token or an API key. | default |

A tool whose capability is not configured is hidden from discovery and answers `provider_not_configured`
if called anyway. Without a model gateway there is no `generate`, `chat`, `translate` or `evaluate`; without an
ASR service there is no `transcribe`; without a usage reader there is no `get_usage`.

Argument limits (abuse bounds for this server, not model limits; the model's own context is 8,092 tokens):

- `generate`: `prompt` up to 6,000 characters, optional `system` up to 2,000, `language` one of `en ha ig yo`, `temperature` 0 to 2 (default 0.1), `max_tokens` 1 to `MCP_GENERATE_MAX_TOKENS` (default 1,024; default value 256).
- `chat`: up to 20 messages, 6,000 characters each, 10,000 in total.
- `translate`: `text` up to 4,000 characters; source and target must differ.
- `transcribe`: `audio_base64` standard base64 without a `data:` prefix, at most `MCP_MAX_AUDIO_BYTES` (default 5 MiB) and 30 seconds; `language` is the spoken language; optional `mime_type` must agree with the audio signature.
- `evaluate`: at most `MCP_EVALUATE_MAX_CASES` cases (default 10), 30 model calls, 90 seconds in total, 2 evaluations running at once on the server. `include_outputs` is `failures` (default), `all` or `none`; previews are cut to 600 characters.
- Unknown fields are rejected. Control characters and blank text are rejected. Errors never echo the rejected value.

Every tool advertises `readOnlyHint`, `destructiveHint` (always false), `idempotentHint` and `openWorldHint`, and a
JSON Schema for its input and its structured output. A test validates every result against the advertised schema.

## Scopes and tokens

Scopes: `models:read`, `project:read`, `generate`, `transcribe`, `evaluate`, `usage:read`. There is no wildcard and no
admin scope. New tokens default to `models:read` and `project:read` only.

A token looks like `fmcp_<8 hex prefix>_<43 characters>`. It is shown once, when issued. Only
`HMAC-SHA256(MCP_TOKEN_PEPPER, token)` is stored, compared in constant time, so a stolen database does not yield usable
tokens. The pepper must be at least 32 bytes. Losing or changing the pepper invalidates every token.
Tokens expire (default 30 days, maximum 90) and revocation takes effect on the next request (nothing is cached).

A project can also be switched off, or its scopes narrowed, without touching tokens: see the policy file below. The
effective scopes of a call are the token's scopes intersected with the project's policy.

## What happens to every tool call

1. The caller must be authenticated.
2. The project's policy must allow MCP (if the policy cannot be read, the call is refused).
3. The tool must exist and the effective scopes must include its scope (`insufficient_scope`; `forbidden` when the policy is off).
4. The capability behind it must be configured (`provider_not_configured`).
5. Rate limits: per client and class, then per project. If the limiter fails, the call is refused (fail closed).
6. The arguments must validate (`invalid_argument` with `details.issues[].path`).
7. The tool runs under a timeout.

Every outcome is written to the audit log with names, sizes, codes and timings. Prompts, model output,
transcripts, audio and argument values are never logged. Unknown tool names are a protocol error (-32602), not a tool result.

Errors that are tool results use one shape: `{"error": {"code", "message", "request_id"}}`, plus `retryable` and `details`
where they help. Codes: `invalid_argument`, `unauthorized`, `forbidden`, `insufficient_scope`, `rate_limited`,
`payload_too_large`, `request_timeout`, `provider_not_configured`, `provider_error`, `provider_timeout`,
`provider_unavailable`, `audio_empty`, `audio_too_large`, `audio_too_long`, `audio_unsupported_type`,
`audio_signature_mismatch`, `audio_corrupt`, `language_unsupported`, `internal_error`.

## The HTTP layer

Every response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
In order:

1. `GET /healthz` (liveness) and `GET /readyz` (readiness: the client store answers). No token, no detail, any Host.
2. The path must be exactly the MCP path (default `/mcp`).
3. Host allow-list (421) and Origin allow-list (403). A request without an Origin header is allowed (command-line and editor clients send none). `OPTIONS` preflight (204) only for allowed origins, never with credentials.
4. `POST` only (405 with `Allow`). Content type must be exactly `application/json` (415).
5. Load shedding: above `MCP_MAX_CONCURRENT_REQUESTS` the answer is 503 with `Retry-After`.
6. Bearer authentication. One `Authorization` header, one shape of 401 for every failure (unknown, malformed, wrong secret, revoked, expired), so the response never tells an attacker which part was wrong. Failed attempts are counted per client address (the socket peer, never `X-Forwarded-For` unless the operator names trusted proxies) and answered with 429 once `MCP_AUTH_FAILURES_PER_MINUTE` is exceeded, without locking out valid tokens. The `Authorization` header is removed before the request reaches the protocol layer.
7. A per-token request limit (`MCP_RATE_LIMIT_REQUESTS_PER_MINUTE`) that applies to every request, including discovery and listing.
8. Body limits: `MCP_MAX_BODY_BYTES` (413), a slow-body timeout `MCP_BODY_READ_TIMEOUT_SECONDS` (408), malformed framing (400).

Host rules: `name` (any port), `name:port`, or `name:*`. Origin rules: `scheme://host[:port]`. No wildcards. Defaults allow
`localhost`, `127.0.0.1`, `[::1]` for hosts and no origins. Rules are validated when settings load, so a typo fails at start.

## Run it

Separate virtual environment (the MCP SDK needs `pydantic>=2.12`):

```powershell
cd C:\Projects\forge\integrations
py -3 -m venv .venv-mcp
.\.venv-mcp\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[mcp]"
```

Create a pepper once and keep it in your secret manager (never in git):

```powershell
$env:MCP_TOKEN_PEPPER = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m forge_integrations.mcp check
```

Issue a token (standard output is the token alone, everything else goes to standard error):

```powershell
$env:FORGE_MCP_TOKEN = python -m forge_integrations.mcp issue-token --project <project id> --name "Claude Code on my laptop" --scopes models:read,project:read,generate --ttl-days 30
```

Start the server (loopback by default):

```powershell
python -m forge_integrations.mcp serve
```

Other commands: `list-tokens --project <id>` (never shows secrets), `revoke-token --project <id> --client-id <id>`,
`check` (validates configuration and prints what would be served). Exit codes: 0 success, 1 operational failure, 2 bad usage or configuration.
`serve` refuses to listen on a non-loopback address without TLS (`--ssl-certfile` and `--ssl-keyfile`), unless you state that a TLS proxy
is in front with `--behind-tls-proxy`.

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_TOKEN_PEPPER` | none (required) | Secret, at least 32 bytes. Keys the token hash. |
| `MCP_HOST`, `MCP_PORT`, `MCP_PATH` | `127.0.0.1`, `8765`, `/mcp` | Where to listen. |
| `MCP_STORE_PATH` | `data/mcp-clients.sqlite3` | SQLite file for clients (owner-only). Not used when the platform provides its own store. |
| `MCP_AUDIT_FILE` | none | Append audit events as JSON lines (owner-only, rotates once at 50 MiB). |
| `MCP_POLICY_FILE` | none | Per-project policy JSON (below). |
| `MCP_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error`. |
| `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` | loopback hosts, no origins | Comma separated rules. |
| `MCP_MAX_BODY_BYTES` | 8 MiB | Must fit a base64 audio payload of `MCP_MAX_AUDIO_BYTES` plus 64 KiB. |
| `MCP_MAX_AUDIO_BYTES` | 5 MiB | Largest audio for `transcribe`. |
| `MCP_RATE_LIMIT_PER_MINUTE` | 60 | Per client, default class. |
| `MCP_RATE_LIMIT_GENERATE_PER_MINUTE` | 20 | Per client, `generate`, `chat`, `translate`. |
| `MCP_RATE_LIMIT_TRANSCRIBE_PER_MINUTE` | 10 | Per client. |
| `MCP_RATE_LIMIT_EVALUATE_PER_MINUTE` | 3 | Per client. |
| `MCP_RATE_LIMIT_PROJECT_PER_MINUTE` | 300 | All tokens of one project together. |
| `MCP_RATE_LIMIT_REQUESTS_PER_MINUTE` | 240 | Any request, per token. |
| `MCP_AUTH_FAILURES_PER_MINUTE` | 20 | Failed authentications per client address. |
| `MCP_GENERATE_MAX_TOKENS` | 1024 | Upper bound for `max_tokens`. |
| `MCP_EVALUATE_MAX_CASES`, `MCP_EVALUATE_MAX_ATTEMPTS`, `MCP_EVALUATE_MAX_SECONDS`, `MCP_EVALUATE_CALL_TIMEOUT_SECONDS`, `MCP_EVALUATE_MAX_CONCURRENT` | 10, 30, 90, 45, 2 | Bounds for `evaluate`. |
| `MCP_TOOL_TIMEOUT_SECONDS` | 120 | Bound for any other tool. |
| `MCP_MAX_CONCURRENT_REQUESTS` | 200 | Beyond this the server answers 503. |
| `MCP_BODY_READ_TIMEOUT_SECONDS` | 30 | Slow-body cut-off. |
| `MCP_TOKEN_DEFAULT_TTL_DAYS`, `MCP_TOKEN_MAX_TTL_DAYS` | 30, 90 | Token lifetime. |
| `MCP_GATEWAY_FACTORY` | none | `package.module:function` returning the model gateway (`generate`, `chat`, `translate`, `evaluate` need it). |
| `MCP_USAGE_FACTORY`, `MCP_PROJECTS_FACTORY` | none | Optional usage reader and project information provider. |
| `MCP_RATE_LIMITER_FACTORY` | none | A shared limiter (for example Redis). Without it limits live in one process, so run one worker. |
| `ASR_SERVICE_URL`, `ASR_SERVICE_TOKEN`, `ASR_ALLOWED_HOSTS`, `ASR_TIMEOUT_SECONDS` | none | The speech worker (enables `transcribe`). |
| `ALLOW_PRIVATE_NETWORK_TARGETS`, `ALLOW_INSECURE_HTTP_TARGETS` | false | Local development only: allow a worker on a private address or without TLS. The CLI prints a warning when on. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy addresses whose `X-Forwarded-For` is trusted. |

Factories are code you supply and trust like the program itself. When one fails, only the exception type is shown
(its message can hold connection details).

### Policy file

```json
{
  "default": { "enabled": true, "allowed_scopes": ["models:read", "project:read"] },
  "projects": { "<project id>": { "enabled": false } }
}
```

Unknown keys are errors, so a typo cannot widen access. The file is read when the server starts.

## Connect a client

Create a token first and put it in an environment variable called `FORGE_MCP_TOKEN` (shown above). Never paste a token
into a file you commit. The formats below were read from each tool's official documentation on 2026-10-10. The files in
`examples/mcp/configs/` are copies. They were checked as valid JSON; the editors themselves were not run here.

### Claude Code

Command line (stores the header in your user configuration, so the token is saved there):

```powershell
claude mcp add --transport http forge http://127.0.0.1:8765/mcp --header "Authorization: Bearer $env:FORGE_MCP_TOKEN"
```

Better, a project file `.mcp.json` that reads the token from the environment when Claude Code starts (safe to commit,
it holds no secret): copy `examples/mcp/configs/claude-code.mcp.json`. Claude Code expands `${VAR}` and
`${VAR:-default}` in `url` and `headers`. Check with `claude mcp list` or `/mcp` inside Claude Code.

### Cursor

`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for every project. Copy `examples/mcp/configs/cursor.mcp.json`.
Cursor resolves `${env:NAME}` in `url` and `headers`.

### VS Code

`.vscode/mcp.json` (top-level key `servers`). Copy `examples/mcp/configs/vscode.mcp.json`. It asks for the token once through a
password prompt (`inputs` with `"password": true`) instead of storing it. The documentation shows these pieces separately;
the combined file is assembled from them.

### Python

`examples/mcp/sdk_client.py` uses the official SDK client. `examples/mcp/call_tools.py` uses only the standard library and
shows the raw requests, which is useful when debugging.

### Claude Desktop

Not verified. Do not rely on a configuration for it from this repository until it has been checked against its own documentation.

## Embedding the server in the platform

`create_mcp_app(deps, settings)` returns an ASGI application. `McpDependencies` takes ports (`ports.py`): the token store
(`McpClientStore`), the rate limiter (`RateLimiter`), the audit sink (`AuditSink`), the model gateway (`ModelGateway`), the ASR provider
(`AsrProvider`), the usage reader (`UsageReader`) and a project information provider. Mount it under the platform's own
server, or run it as its own process with the factory variables above. The SQL for the client table is in
`integration-contract.md`. Token management for the dashboard calls `McpTokenService.issue`, `list_clients` and `revoke`.

## Limits of what was tested

- Tested: both protocol revisions over a real socket with a hand-built client and with the official `mcp` 2.2.0 client; in-memory SDK client for schemas and annotations; the HTTP defences; the command line, including a real server start; the audit trail's content.
- Not tested: the editors' own clients (Claude Code, Cursor, VS Code), a shared Redis limiter (only an in-memory fake of the same port), more than one server process, TLS termination in front of the server, Windows service installation.
- Tested only with a scripted model gateway. No real N-ATLAS text generation was run: that needs the platform's gateway or N-ATLAS API credentials.
