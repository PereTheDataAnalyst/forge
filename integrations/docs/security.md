# Security notes for the advanced integrations

This page describes what the Evaluation, Voice and MCP modules do to stay safe, and what they do not do. It is an engineering
description, not a certification. Nothing here claims SOC 2, ISO 27001, HIPAA, GDPR or any other certification or compliance status,
and no product copy should.

## Rules that every module follows

- No secret has a default value. Secrets come from the environment only and never appear in `repr`, logs, errors, audit records or exports.
- Redaction (`security/redaction.py`) replaces values stored under sensitive keys (authorization, cookies, tokens, passwords, secrets, pepper, private keys, token hashes, audio) and well-known secret shapes inside strings, truncates long strings, and renders raw bytes only as a length. Its patterns run in linear time.
- Nothing logs prompts, model output, transcripts, audio, argument values, API keys or authorization headers. Audit and log records carry names, sizes, codes, timings and identifiers.
- Errors are client-safe: a stable `code`, a plain `message`, a `request_id`. No stack traces, no file paths, no connection strings, no rejected input echoed back.
- No invented behaviour: no fake model responses, no fake metrics. The only scripted model in the repository is the test double used by tests.

## Tokens

- Format `fmcp_<8 hex>_<43 characters>`, 256 bits of secret. Shown once.
- Only `HMAC-SHA256(pepper, token)` is stored; compared in constant time. A token for an unknown prefix still spends the same work (`burn`), so timing does not reveal which prefixes exist.
- The pepper is at least 32 bytes and lives in the operator's secret manager. Rotating it invalidates every token (there is no partial rotation).
- Scopes are explicit and narrow. The default is read-only. There is no wildcard and no admin scope.
- Tokens expire (30 days by default, 90 at most). Revocation is checked on every request, with no cache.
- A project can disable MCP or narrow scopes without touching its tokens. If the policy cannot be read the call is refused.

## What an MCP tool can and cannot do

- Every tool is authenticated, authorised against one scope, validated with strict types and closed schemas, rate limited, run under a timeout and audited.
- The tool set is fixed in code. There is no tool to create or delete projects, rotate or list keys, change settings, run a database query, reach infrastructure or run a shell, and a test asserts those names do not exist (`delete_project`, `rotate_all_keys`, `run_shell`, `sql` are protocol errors).
- Text a model or a user supplies is data. No tool follows instructions found in it, no tool fetches a URL or reads a file, and a test confirms hostile text is passed to the model unchanged, is never echoed in an error and never appears in the audit trail.
- Tool results are bounded. Evaluation output previews are cut at 600 characters, and the whole result is capped.
- Calls are rate limited per client and per project, so many tokens cannot multiply a quota. If the limiter fails, calls fail closed.

## HTTP hardening (MCP server and ASR worker)

- Host and Origin allow-lists (DNS rebinding and cross-site calls), exact path, exact content type, `POST` only.
- One shape of 401 for every authentication failure; a per-address failed-attempt counter that answers 429 without locking out valid tokens; the client address is the socket peer, never a spoofable header unless the operator names trusted proxies.
- Request bodies are bounded by size and by time; slow clients are cut off; the number of concurrent requests is bounded and excess load gets a quick 503 instead of a queue.
- The `Authorization` header is removed before the request reaches the protocol layer.
- `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` on every response.
- The server refuses to listen on a public address without TLS unless the operator says a TLS proxy is in front. Terminating TLS is the operator's job.

## Uploads and audio

- Format by file signature, never by name; the declared content type must agree; size and duration limits; malformed files refused.
- Decoding runs in an `ffmpeg` subprocess with a fixed argument list, a forced demuxer, a protocol allow-list limited to local files, stream types dropped, and caps on input duration, output duration and output size, plus a timeout that kills it.
- Files are written under random names with an allow-listed suffix, mode 0600 on POSIX, and removed when the request ends, including on errors. Stale files are swept. Uploads are never executed and never opened by a user-supplied path.
- The ASR worker authenticates before it reads the body.

## Outbound requests (SSRF)

- Forge calls only hosts an operator allow-listed. User input never forms a URL. The MCP tools accept no URLs.
- The host is resolved and checked before every call; link-local and cloud metadata addresses are always refused; private and loopback addresses are refused unless the operator enabled them for development; the connection is pinned to the validated address so a changing DNS answer cannot redirect it; redirects are never followed; reply sizes are bounded.

## Evaluation

- Datasets are validated strictly and bounded (cases, prompt sizes, attempts). Every problem is reported together.
- Runs are scoped by project: a run belonging to another project is indistinguishable from one that does not exist.
- CSV export neutralises spreadsheet formula injection in model output.
- `no_secrets` and `no_contact_details` checks exist so a dataset can test whether a model leaks such strings. They are pattern checks.

## Tested

The suite includes tests aimed at these properties (see `tests/test_security.py`, `test_mcp_http.py`, `test_mcp_tools.py`, `test_mcp_service.py`, `test_hostcheck.py`, `test_asr_service.py`, `test_voice_*.py`): redaction of keys and shapes; token handling and timing work; scope and policy enforcement; the error shape; hostile input; every HTTP refusal path over a real socket; load shedding; slow and disconnecting clients; file signature and size limits; temp-file cleanup; outbound allow-listing, address checks, DNS changes and TLS; audit contents.

## Not done, and residual risks

- No independent penetration test or security review has been carried out. These tests are the author's own.
- The default rate limiter lives in one process. With several MCP workers, limits are per worker unless a shared limiter is supplied (`MCP_RATE_LIMITER_FACTORY`). The Redis adapter has not been written or run against a real Redis or Memurai.
- Operator-supplied factories (`MCP_*_FACTORY`) are code that runs with the server's privileges. Only point them at code you trust.
- `ffmpeg` is a large program with its own vulnerabilities. Keep it up to date on the worker machine and keep the worker on a private network.
- Windows does not enforce POSIX file modes. On Windows rely on the folder permissions of the user running the services (the owner-only behaviour is verified on Linux only).
- The SQLite client store is for a single node. A multi-node deployment should use the platform's database (`integration-contract.md`).
- Denial of service is bounded, not eliminated: limits and load shedding reduce it, a network-level attack needs network-level controls.
- The language-model gateway, the platform's own API keys and the dashboard are outside these modules and are covered by the core platform's security work.
