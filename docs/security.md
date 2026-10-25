# Security

Security is a design requirement from the first commit. This page maps each control to its code and lists what is not done yet. Forge has not been independently audited and holds no security certifications.

## Implemented controls

| Area | Control | Code |
| --- | --- | --- |
| API keys | 256-bit secrets, shown once, stored only as HMAC-SHA-256 with a server-side pepper, public prefix for identification, scopes, expiry, revoke, rotate with optional grace period, `last_used_at` | `app/security/tokens.py`, `app/services/api_key_service.py` |
| Key verification | Constant-time comparison, equalised timing for unknown prefixes, revoked and expired keys rejected | `authenticate_api_key` |
| Passwords | Argon2id, policy (length, common passwords, email part), timing equalisation for unknown accounts | `app/security/passwords.py`, `password_policy.py` |
| Brute force | Per-account and per-address lockout counters in Redis, per-address request limit | `auth_service.authenticate`, `cache/rate_limit.py` |
| Sessions | Opaque 256-bit token in an HTTP-only, SameSite=Lax cookie, only its SHA-256 stored, idle and absolute expiry, revocation on logout | `auth_service`, `api/v1/auth.py` |
| CSRF | Token derived from the session with HMAC, verified in constant time on every state-changing cookie-authenticated request, plus an Origin allowlist | `app/api/deps.py` |
| Gateway isolation | A session cookie never authorises the gateway and an API key never authorises account endpoints | `deps.get_api_principal` |
| Authorization | Projects are private to their owner. Missing and foreign projects both return 404. | `project_service.get_owned_project` |
| Input validation | Pydantic schemas with `extra="forbid"`, size limits, semantic checks | `app/schemas`, `gateway_service` |
| Request size | Limit by `Content-Length` and while streaming, per-path overrides | `middleware/body_limit.py` |
| Outbound requests | One fixed destination from configuration, redirects off, proxy environment ignored | `providers/natlas.py` |
| Errors | One safe shape with a request ID. Unhandled exceptions become a generic 500 and the details stay in the server log. | `core/errors.py`, `middleware/access_log.py` |
| Logging | Structured logs with automatic redaction of API keys, bearer tokens, Hugging Face tokens, URL credentials and sensitive keys. Request logs never contain prompts or responses. The Evaluation Lab stores dataset prompts and model outputs by design, see [evaluation-lab.md](evaluation-lab.md). | `core/redaction.py`, `core/logging.py` |
| Headers | `nosniff`, frame denial, referrer policy, permissions policy, COOP/CORP, strict CSP and `no-store` for API responses, HSTS in production | `middleware/security_headers.py` |
| Secrets | Environment only. Placeholder or short secrets are refused outside tests. Validation errors hide input values. | `core/config.py` |
| Audit | Registration, sign-in, failed sign-in, logout, project and key events | `services/audit.py` |
| Telemetry bounds | Only supported model ids are recorded, so clients cannot create unbounded rows. Throttled requests are not written to the database. | `gateway_service._record` |
| Worker | Shared bearer token, constant-time check, bounded queue and concurrency, no content logging | `inference/natlas-worker` |
| MCP credentials | A client is a platform API key with the `mcp.invoke` scope plus a client row that lists the tools it may use. Both are looked up on every request and nothing is cached, so revoking either stops access on the next request. A key with `mcp.invoke` but no client row is refused. The key has no access to the REST gateway. | `modules/mcp/auth.py`, `modules/mcp/store.py` |
| MCP server | Exact `Host` and `Origin` allow-lists, bounded bodies, no administrative tools by construction, per-client limits in Redis, audit events with names and codes only (never prompts, output, audio or tokens), the platform request ID and client address | `integrations/src/forge_integrations/mcp`, `modules/mcp/runtime.py` |
| Voice | Audio is checked by size, signature and length before the worker sees it, and is never stored or logged. The worker address is checked against private-network, plain-http and redirect rules. Only a format name, never client text, is stored. | `modules/voice`, `integrations/src/forge_integrations/voice` |
| Request logs | Owner session only, ownership checked first, every filter validated, rows hold no prompts or replies | `api/v1/request_logs.py`, `services/request_log_service.py` |
| SDK clients | Redirects are never followed, plain http only for loopback, the key is never printed or put in an error, responses and uploads are size-bounded | `sdk/` |

## Tests that exercise these controls

`backend/tests/test_api_flow.py` covers hashing at rest and show-once keys, CSRF and Origin rejection, lockout, tenant isolation, scope enforcement, the cookie-versus-key boundary, rate limiting, body size limits, audit entries without secrets, and unbounded-cardinality protection. `test_primitives.py` covers redaction, client IP resolution, Origin normalisation, token and CSRF primitives and the password policy, and passes in the build sandbox. `test_natlas_provider.py` covers every worker error mapping and response validation.

## Known gaps

| Gap | Impact | Plan |
| --- | --- | --- |
| No email verification or password reset | Accounts are unverified and recovery is manual | Needs an email transport decision |
| No cleanup of expired sessions, old request logs or audit rows | Tables grow without bound | Scheduled job and retention settings |
| Fixed-window rate limiter | Bursts up to twice the limit across a window edge | Sliding window if needed |
| The web app's CSP must be set by the reverse proxy | The API sets its own CSP only for JSON | See [deployment.md](deployment.md) |
| No dependency audit has run | Unknown advisories | `npm audit` and `pip-audit` on a networked machine |
| Dependency pins are written by hand | `backend/constraints.txt` fixes the versions the image installs, but it was copied from a build log and has no hashes. The versions of four packages that arrive with the MCP SDK are not pinned | Generate a hashed lock file on a networked machine, and add the four missing pins from the first build that installs the SDK |
| The MCP server has not run against the real SDK where it was written | The mount, the lifespan bridge and the authenticator are tested with fakes only | Run `scripts\probe-mcp-compat.ps1`, then smoke test step 6f against the deployment |
| No speech worker has run | Voice cannot transcribe, and nothing says how accurate the models are in practice | Deploy `integrations/asr-service` and run the smoke test |
| Registration reveals whether an email exists | Account enumeration | Accepted, rate limited |
| No two-factor authentication, organisations or roles | Single-owner projects only | Roadmap |
| Worker uses a shared bearer token | No mutual TLS | Keep it on a private network |
| IP addresses are stored in sessions and the audit log | Personal data | Retention and hashing policy |

## Reporting

See [SECURITY.md](../SECURITY.md).
