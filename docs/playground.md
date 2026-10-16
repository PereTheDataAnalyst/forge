# Playground

The Playground (`/app/playground`) is a page in the web app for trying N-ATLAS from the browser. It uses the same gateway as the API, reached with your signed-in session instead of an API key.

## What it does

| Feature | Behaviour |
| --- | --- |
| Project | Every request is counted against a project you own, so it appears in that project's request logs and usage. |
| Model | Lists the text model the deployment serves, with its live status from the provider. |
| Conversation | Messages stay in the browser tab. Reloading the page clears them. Each request sends the system instruction (if any) and the whole conversation. |
| Parameters | Temperature 0 to 2, top p above 0 and at most 1, maximum new tokens 1 to 2048, repetition penalty 1 to 2. The defaults are 0.1, 1, 512 and 1.12. The server enforces the same limits and the deployment's token cap (`NATLAS_MAX_TOKENS_LIMIT`). |
| Language | Optional label for English, Hausa, Igbo or Yoruba. It is recorded with the request. It does not change how the model answers. |
| Stop | Cancels the wait in your browser and puts the unanswered message back in the box. The model host may still finish generating that reply. |
| Regenerate | Sends the last question again, without the previous answer. |
| Details | The exact request body, the raw response, the request ID, token usage, gateway time, round-trip time, and the same request as cURL, PowerShell, JavaScript and Python for use with an API key. |
| Streaming | In development. A reply appears when generation finishes. |

## How it is protected

- It needs a signed-in session. An API key does not open it, and it never receives or shows an API key.
- Sending a message is a state-changing request, so it needs the CSRF header and an allowed `Origin`, like every other browser call.
- The project must belong to you. A project that is missing, archived or owned by someone else returns 404 `project_not_found`.
- The call goes through `GatewayService.chat`, so scope checks, message validation, rate limiting, request logging and usage metering are the same as for API keys. The rate limit is `GATEWAY_RATE_LIMIT_PER_MINUTE`, counted per signed-in user.

## What is stored

The request log records the project, user, model, language, status, outcome and timings, with `source` set to `playground`. Prompts and replies are returned to your browser and are not stored by the platform.

## Endpoints

| Endpoint | Description |
| --- | --- |
| `GET /api/v1/projects/{id}/playground/models` | Text models with live status. |
| `POST /api/v1/projects/{id}/playground/chat` | Same body and response as `POST /api/v1/chat`. |

Errors use the standard shape. While the model host is away the response is 503 `provider_unavailable` with a request ID, and the page shows both. See [api-reference.md](api-reference.md).

## Layout

On a wide screen the page shows settings, the conversation and request details side by side. Below 1500 pixels the details panel starts closed so the conversation has room; "Show details" opens it. On a phone the page shows one section at a time, chosen with the tabs under the title.

## Tests

- `backend/tests/test_playground.py` runs the real routes, session authentication, CSRF and Origin checks, ownership, gateway, request logging and rate limiting against PostgreSQL and Redis. The model is replaced by a scripted provider that exists only in `backend/tests/provider_double.py`. Run it with `scripts\check.ps1`.
- `scripts\smoke-day1.ps1` has a Playground step that runs against any address, including the live one.
