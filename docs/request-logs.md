# Request logs and charts

The Request logs page lets a project owner search and filter every request the platform recorded for a project, and chart usage and latency over time. It reads the `requests` table, which the gateway writes. Nothing on the page is estimated or sampled.

Everything here needs the signed-in browser session. An API key cannot read logs.

## What is recorded

One row per request that reached the gateway, whoever sent it:

| `source` | Where it comes from |
| --- | --- |
| `api_key` | `POST /chat` with a project API key |
| `playground` | The Playground page |
| `evaluation` | Evaluation Lab runs, one row per model call |
| `voice` | The Voice page and `POST /audio/transcriptions` |
| `mcp` | Tool calls made through the MCP server |

A row has the request ID, time, source, endpoint, method, HTTP status, outcome, error code, total latency, provider latency, model, language, token counts and the API key that was used. It never has a prompt, a reply, a transcript or audio.

**Not recorded.** A request that the platform rate limiter rejected with 429 never reaches the gateway, so it has no row. Failed sign-ins and requests with a bad key have no row either. The charts show what the gateway handled, not everything that was attempted.

## Endpoints

All three take the same filters. Project ownership is checked first: a project that is not yours is a 404.

| Endpoint | Returns |
| --- | --- |
| `GET /projects/{id}/requests` | A page of rows, newest first, and `next_cursor` |
| `GET /projects/{id}/requests/metrics` | Totals, a zero-filled time series and breakdowns |
| `GET /projects/{id}/requests/facets?range=` | Models, error codes and endpoints seen in the window. It ignores the other filters, so a list of choices does not shrink when you pick one |

| Filter | Values |
| --- | --- |
| `range` | `1h`, `6h`, `24h` (default), `7d`, `30d`, `90d` |
| `outcome` | `success` or `error` |
| `source` | `api_key`, `playground`, `mcp`, `evaluation`, `voice` |
| `language` | `en`, `ha`, `ig`, `yo` |
| `model`, `endpoint` | Exact match, up to 120 characters |
| `error_code` | Exact match, lower case letters, digits and underscores |
| `status_code` | 100 to 599 |
| `api_key_id` | A key ID from the project |
| `request_id` | Starts with. Letters, digits, dots, dashes and underscores, up to 64 |
| `min_latency_ms` | 0 to 3600000 |

The list also takes `limit` (1 to 100, default 50) and `cursor`. A bad value is a 422 with a stable code: `validation_error`, `invalid_cursor` or `invalid_request_id`.

### Paging

The cursor names the last row of a page (its time and ID), so a page never repeats or skips a row even while new requests arrive. Treat it as opaque. Start again from the first page if the filters change.

### Windows and buckets

Buckets are aligned to the Unix epoch, so they start on a whole UTC hour or day.

| `range` | Bucket | Points |
| --- | --- | --- |
| `1h` | 5 minutes | 13 |
| `6h` | 15 minutes | 25 |
| `24h` | 1 hour | 25 |
| `7d` | 6 hours | 29 |
| `30d` | 1 day | 31 |
| `90d` | 3 days | 31 |

The series has a point for every bucket in the window, with zeros where nothing happened, so a gap in traffic is a visible gap.

### What the numbers mean

- **Latency** is the total time the platform spent on a **successful** request, in milliseconds: average, median (p50) and 95th percentile (p95). Failed requests are counted but not timed, because a request that fails fast or times out would distort the picture. With no successful request in a window the latency is `null`, never zero.
- **Tokens** are the model's own counts for requests that returned them.
- **Success rate** is successful requests divided by all recorded requests, or `null` with no requests.
- Breakdowns list up to ten values by source, model, language, error code, endpoint and HTTP status. The error code breakdown counts failed requests only.

## Limits

- Queries run under the database statement timeout (`DB_STATEMENT_TIMEOUT_MS`) and use the `(project_id, created_at)` index.
- **There is no retention policy yet.** The `requests` table grows without bound. A retention job is on the list of work to do. Until it exists, watch the table size.

## Verification

- The cursor, range, bucket and zero-fill logic is tested without a database (`backend/tests/test_request_log_math.py`).
- The SQL (filters, percentiles, grouping, paging) and the routes are covered by `backend/tests/test_request_logs.py`. These are integration tests that need PostgreSQL and Redis, and were written without a database to run them against. The first `check.ps1` run on your machine is their first run.
- Smoke test step 6d checks the endpoints against a running deployment.
