# API reference

Base path: `/api/v1`. Every endpoint listed here exists. All responses carry `X-Request-ID`. Errors use one shape:

```json
{ "error": { "code": "validation_error", "message": "...", "request_id": "req_...", "details": [{ "field": "messages", "message": "..." }] } }
```

`details` appears only for validation errors. SDK equivalents are not available yet.

## Gateway (API key)

Authenticate with `Authorization: Bearer nfk_<identifier>_<secret>`. Keys belong to a project and carry scopes.

### `GET /models`

Scope `models.read`. Lists models available through the gateway.

```json
{ "data": [ { "id": "NCAIR1/N-ATLaS", "kind": "llm", "display_name": "N-ATLaS LLM", "architecture": "LlamaForCausalLM (Llama-3 8B base)", "parameters": "8B", "languages": ["en", "ha", "ig", "yo"], "context_length_tokens": 8092, "license": "N-ATLaS Terms of Use (Open-Source Research and Innovation License)", "source_url": "https://huggingface.co/NCAIR1/N-ATLaS", "attribution": "N-ATLaS is an initiative of ...", "status": "available" } ] }
```

`status` is `available`, `loading`, `not_configured` or `unavailable`. Errors: 401 `missing_api_key`, `invalid_api_key`, `api_key_revoked`, `api_key_expired`; 403 `insufficient_scope`, `project_archived`; 429 `rate_limited`.

### `POST /chat`

Scope `chat.create`. Generates a reply. Unknown fields are rejected.

| Field | Required | Description |
| --- | --- | --- |
| `messages` | yes | 1 to 100 items of `{ "role": "system" \| "user" \| "assistant", "content": string }`. A system message is allowed only first. The last message must be `user`. |
| `model` | no | A model id from `GET /models`. Defaults to the configured model. |
| `max_tokens` | no | 1 to 8192, default 512, capped by the deployment (2048 by default). |
| `temperature` | no | 0 to 2, default 0.1. Zero selects greedy decoding. |
| `top_p` | no | Above 0 and at most 1, default 1.0. |
| `repetition_penalty` | no | 1 to 2, default 1.12. |
| `language` | no | `en`, `ha`, `ig` or `yo`. Recorded in logs and metrics only. |

```json
{ "request_id": "req_...", "model": "NCAIR1/N-ATLaS", "message": { "role": "assistant", "content": "..." }, "finish_reason": "stop", "usage": { "prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42 }, "latency_ms": 1840 }
```

`finish_reason` is `stop` or `length`. `prompt_tokens` counts the prompt after chat templating. `completion_tokens` excludes the end-of-turn token. Response headers include `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`.

Errors: the gateway errors above, plus 422 `validation_error`, `invalid_messages`, `invalid_parameters`, `input_too_long`, `context_length_exceeded`; 404 `model_not_found`; 413 `payload_too_large`; 503 `provider_not_configured`, `provider_unavailable`, `provider_loading`, `provider_busy` (with `Retry-After` when known); 504 `provider_timeout`; 502 `provider_misconfigured`, `provider_invalid_response`, `provider_error`.

Example:

```powershell
$body = '{"messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":64}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/chat `
  -Headers @{ Authorization = "Bearer $env:FORGE_API_KEY" } `
  -ContentType "application/json; charset=utf-8" -Body $body
```

## Health

| Endpoint | Auth | Description |
| --- | --- | --- |
| `GET /health/live` | none | `{ "status": "ok", "version": "..." }` |
| `GET /health/ready` | none | Checks PostgreSQL, Redis and the provider. 200 for `ok` or `degraded` (provider not connected), 503 for `unavailable` (database or Redis down). |

## Account and web app endpoints (cookie session)

These serve the web app. State-changing calls need the `X-CSRF-Token` header (value of the `forge_csrf` cookie) and an allowed `Origin`. Failures: 401 `not_authenticated` or `session_expired`; 403 `csrf_validation_failed` or `origin_not_allowed`.

| Endpoint | Purpose | Notes |
| --- | --- | --- |
| `POST /auth/register` | Create an account and sign in | Body `{ email, password, display_name? }`. 201. Errors 403 `registration_closed`, 409 `email_already_registered`, 422 `weak_password`, 429 `auth_rate_limited`. |
| `POST /auth/login` | Sign in | Body `{ email, password }`. Errors 401 `invalid_credentials`, 403 `account_disabled`, 429 `account_temporarily_locked`, `ip_temporarily_locked`, `auth_rate_limited`. |
| `POST /auth/logout` | Revoke the session | 204. |
| `GET /auth/me` | Current user | Restores the CSRF cookie if missing. |
| `GET /projects` | List your projects | `{ "data": [...] }` |
| `POST /projects` | Create | Body `{ name, description? }`. 201. 409 `project_limit_reached` at 20 active projects. |
| `GET /projects/{id}` | Read | 404 `project_not_found` for missing or foreign projects. |
| `PATCH /projects/{id}` | Update | Body `{ name?, description? }`. |
| `DELETE /projects/{id}` | Archive | 204. The project's keys stop working. |
| `GET /projects/{id}/api-keys` | List keys | Never includes the full key. |
| `POST /projects/{id}/api-keys` | Create a key | Body `{ name, scopes?, expires_in_days? }`. 201 with the full `key`, shown once. 409 `api_key_limit_reached` at 20 active keys. 422 `invalid_scope`. |
| `POST /projects/{id}/api-keys/{key_id}/revoke` | Revoke | Idempotent. |
| `POST /projects/{id}/api-keys/{key_id}/rotate` | Rotate | Body `{ grace_period_seconds? }` (0 to 86400). Returns `new_key` with the full key and `previous`. 409 `api_key_not_active`. |
| `GET /dashboard/summary?days=30` | Totals and recent activity | `days` from 1 to 90. All figures come from stored request data. |
| `GET /projects/{id}/playground/models` | Text models with live status | Used by the Playground. `{ "data": [...] }`. 404 `project_not_found` for missing or foreign projects. |
| `POST /projects/{id}/playground/chat` | Generate a reply with your session | Same body and response as `POST /chat`, see [playground.md](playground.md). Needs the CSRF header. Logged with source `playground`. 503 `provider_unavailable` while the model host is away. |

## Rate limits

Gateway: `GATEWAY_RATE_LIMIT_PER_MINUTE` per API key (per signed-in user for the Playground), fixed one-minute window. Authentication: `AUTH_RATE_LIMIT_PER_MINUTE` per client address, and an account locks for `LOGIN_LOCKOUT_MINUTES` after `LOGIN_MAX_FAILURES` failed sign-ins. A limited request returns 429 with `Retry-After`.
