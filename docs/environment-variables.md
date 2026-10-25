# Environment variables

This table is generated from `backend/app/core/config.py`, so it matches the code. `.env.example` shows the same variables with comments.

Local development reads `.env` at the repository root. Production should provide real environment variables or secrets from the host and no `.env` file. Environment variables always take priority over `.env`.

| Variable | Default | Description |
| --- | --- | --- |
| `APP_ENV` | `development` | Runtime environment: development, test or production. Production enables strict validation (secure cookies, https URLs) and HSTS. |
| `APP_NAME` | `N-ATLAS Forge` | Display name used in the API documentation. |
| `LOG_LEVEL` | `INFO` | Log verbosity: DEBUG, INFO, WARNING or ERROR. |
| `LOG_FORMAT` | `json` | json for log collectors or console for readable terminal output. |
| `ENABLE_API_DOCS` | `False` | Serves Swagger UI at /api/docs and the schema at /api/openapi.json. Keep false in production. |
| `DATABASE_URL` | `required` | PostgreSQL connection URL. postgres:// and postgresql:// are rewritten to the asyncpg driver. |
| `DB_POOL_SIZE` | `10` | Connections kept in the pool. |
| `DB_MAX_OVERFLOW` | `10` | Extra connections allowed beyond the pool size under load. |
| `DB_POOL_TIMEOUT_SECONDS` | `10` | How long a request waits for a free connection before failing. |
| `DB_STATEMENT_TIMEOUT_MS` | `15000` | PostgreSQL cancels any statement running longer than this. |
| `REDIS_URL` | `required` | Redis connection URL. Used for rate limiting and lockout counters only. |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `2.0` | Connect and read timeout for Redis calls. |
| `SECRET_KEY` | `required` | At least 32 characters. Signs CSRF tokens. Placeholder values are rejected outside tests. |
| `API_KEY_PEPPER` | `required` | At least 32 characters. Keys the HMAC that protects stored API keys. Changing it invalidates all existing keys. |
| `COOKIE_SECURE` | `False` | Marks cookies Secure. Must be true in production. |
| `SESSION_COOKIE_NAME` | `forge_session` | Name of the HTTP-only session cookie. In production consider the __Host- prefix. |
| `CSRF_COOKIE_NAME` | `forge_csrf` | Name of the readable CSRF cookie that the web app echoes in X-CSRF-Token. |
| `SESSION_IDLE_TTL_HOURS` | `72` | A session ends after this many hours without activity. |
| `SESSION_ABSOLUTE_TTL_HOURS` | `24 * 14` | A session ends after this many hours regardless of activity. |
| `REGISTRATION_ENABLED` | `True` | Set false to close sign-ups. |
| `PUBLIC_BASE_URL` | `http://localhost:5173` | Browser origin of the web app. Allowed for state-changing requests. Must be https in production. |
| `CORS_ALLOWED_ORIGINS` | `empty` | Extra browser origins allowed to call the API, comma separated. Wildcards are rejected. |
| `TRUSTED_PROXY_COUNT` | `0` | Reverse proxies you trust in front of the API, used to read the client IP from X-Forwarded-For. |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | Largest accepted request body. Extension modules may raise it for specific paths. |
| `FRONTEND_DIST_DIR` | `unset` | Directory that holds the built web app (`frontend/dist`). When set, this process serves the web app next to the API from one origin, answering only paths that no API route matched. The production image sets it to `/app/frontend-dist`. Leave it unset in development, where the Vite server serves the web app. |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `20` | Sign-in and registration attempts per client address per minute. |
| `LOGIN_MAX_FAILURES` | `5` | Failed sign-ins before an account is locked temporarily. A client address locks at four times this value. |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Length of the lockout and of the failure counting window. |
| `GATEWAY_RATE_LIMIT_PER_MINUTE` | `60` | Requests per API key per minute, fixed window. |
| `RATE_LIMIT_FAIL_OPEN` | `False` | When Redis is unavailable: false rejects requests, true lets them through. |
| `NATLAS_MODEL_ID` | `NCAIR1/N-ATLaS` | Identifier of the model served by the worker. Must match the worker's NATLAS_WORKER_MODEL_ID. |
| `NATLAS_INFERENCE_URL` | `unset` | Base URL of the inference worker. Set together with the token, or leave both unset. |
| `NATLAS_INFERENCE_TOKEN` | `unset` | Bearer token shared with the worker. Never logged. |
| `NATLAS_ALLOW_PLAINTEXT_REMOTE` | `False` | Allows an http:// worker URL on a non-loopback host in production. Only for a private network. |
| `NATLAS_CONNECT_TIMEOUT_SECONDS` | `3.0` | Time allowed to connect to the worker. |
| `NATLAS_REQUEST_TIMEOUT_SECONDS` | `120.0` | Time allowed for the worker to answer a generation request. |
| `NATLAS_HEALTH_CACHE_SECONDS` | `10.0` | How long a worker health probe result is reused. |
| `NATLAS_DEFAULT_MAX_TOKENS` | `512` | max_tokens used when a request omits it. |
| `NATLAS_MAX_TOKENS_LIMIT` | `2048` | Largest max_tokens a request may ask for. |
| `NATLAS_MAX_MESSAGES` | `50` | Largest number of messages in one request. |
| `NATLAS_MAX_INPUT_CHARS` | `32000` | Largest combined message length in characters. The worker enforces the exact token limit. |
| `NATLAS_DEFAULT_TEMPERATURE` | `0.1` | temperature used when a request omits it. Mirrors the official model card example. |
| `NATLAS_DEFAULT_TOP_P` | `1.0` | top_p used when a request omits it. |
| `NATLAS_DEFAULT_REPETITION_PENALTY` | `1.12` | repetition_penalty used when a request omits it. Mirrors the official model card example. |
| `EVAL_MAX_CASES` | `100` | Most cases in one evaluation dataset. |
| `EVAL_MAX_CONCURRENCY` | `2` | Model calls one evaluation run makes at once. Keep it low: the model host handles one generation at a time. |
| `EVAL_CALL_TIMEOUT_SECONDS` | `90.0` | Time allowed for one model call during a run. |
| `EVAL_MAX_CONCURRENT_RUNS_PER_PROJECT` | `1` | Runs one project may have in progress. |
| `EVAL_RUNS_PER_HOUR` | `20` | Runs one user may start per hour. |
| `EVAL_MAX_RUN_SECONDS` | `900` | A run that takes longer is stopped and keeps the results it has. |
| `EVAL_MAX_EVALUATIONS_PER_PROJECT` | `50` | Active datasets per project. |
| `ASR_SERVICE_URL` | unset | Address of the speech worker (`integrations/asr-service`). Set together with `ASR_SERVICE_TOKEN`, or leave both unset. Until then Voice answers 503 `provider_not_configured`. See [voice.md](voice.md). |
| `ASR_SERVICE_TOKEN` | unset | The worker's bearer token. |
| `ASR_ALLOWED_HOSTS` | empty | Extra host names the worker may use, comma separated. The host in `ASR_SERVICE_URL` is always allowed. |
| `ASR_TIMEOUT_SECONDS` | `60.0` | Per call to the worker, 5 to 300. |
| `ASR_ALLOW_PRIVATE_NETWORK` | `false` | Allow a worker on a loopback or private network address. Needed for a local worker. |
| `ASR_ALLOW_PLAINTEXT_REMOTE` | `false` | In production, allow an `http://` worker URL on a non-loopback host. Private networks only. |
| `VOICE_MAX_UPLOAD_BYTES` | `10485760` | Largest audio upload, 65536 to 26214400. |
| `VOICE_MAX_SECONDS` | `30` | Longest clip, 1 to 30. |
| `VOICE_RATE_LIMIT_PER_MINUTE` | `10` | Audio requests per API key or signed-in user, 1 to 600. |
| `VOICE_MAX_CONCURRENT` | `4` | Uploads in flight per process, 1 to 32. More are turned away with 503 `provider_busy`. |
| `MCP_ENABLED` | `true` | Serve the MCP server at `/mcp`. `false` makes `/mcp` answer 503 `mcp_disabled`. See [mcp.md](mcp.md). |
| `MCP_ALLOWED_HOSTS` | empty | Extra `Host` header values `/mcp` accepts, comma separated. The host of `PUBLIC_BASE_URL` and the loopback names are always accepted. |

## Test-only variables

These are read from the process environment by pytest. `scripts/check.ps1` loads them from `.env`.

| Variable | Description |
| --- | --- |
| `TEST_DATABASE_URL` | PostgreSQL URL of a dedicated database. Its name must contain `test`, or the suite refuses to run. Integration tests are skipped when unset. |
| `TEST_REDIS_URL` | Redis URL on a logical database numbered 10 or higher, because the suite flushes it. Defaults to `redis://127.0.0.1:6379/15`. |

## Inference worker variables

The worker is a separate process with its own `.env` in `inference/natlas-worker`. See `inference/natlas-worker/.env.example`.

| Variable | Description |
| --- | --- |
| `NATLAS_WORKER_TOKEN` | Required. At least 32 characters. The gateway sends it as a Bearer token. Same value as the gateway's `NATLAS_INFERENCE_TOKEN`. |
| `NATLAS_WORKER_MODEL_ID` | Hugging Face repository. Default `NCAIR1/N-ATLaS`. |
| `NATLAS_WORKER_DTYPE` | `auto`, `float16`, `bfloat16` or `float32`. `auto` uses float16 on CUDA and bfloat16 on CPU. |
| `NATLAS_WORKER_CONTEXT_LIMIT_TOKENS` | Prompt tokens plus new tokens must not exceed this. Default 8092, the value in the model card. |
| `NATLAS_WORKER_MAX_CONCURRENCY` | Generations running at once. Default 1. |
| `NATLAS_WORKER_MAX_QUEUE` | Requests allowed to wait. Further requests receive `busy`. Default 4. |
| `NATLAS_WORKER_MAX_GENERATION_SECONDS` | Time limit passed to generation. Default 120. |
| `HF_TOKEN` | Hugging Face access token with read permission, needed to download the gated model. |
