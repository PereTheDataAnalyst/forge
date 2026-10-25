# SDKs

Typed clients for the gateway, in [Python](../sdk/python/README.md) and [JavaScript and TypeScript](../sdk/javascript/README.md). Both wrap `models.list`, `chat.create` and `audio.transcribe`, return the **request ID** on every result and every error, and use no runtime dependencies.

| | Python | JavaScript and TypeScript |
| --- | --- | --- |
| Folder | `sdk/python` | `sdk/javascript` |
| Needs | Python 3.9 or newer | Node 18 or newer (also Bun and Deno) |
| Install | `python -m pip install ./sdk/python` | `npm pack` in the folder, then install the tarball |
| Async | `AsyncForge`, on worker threads | Every call returns a promise |

**Status.** `0.1.0`. Neither package is published to PyPI or npm, and both names (`natlas-forge`) are provisional. Choose the real names before publishing; nothing in the code depends on them.

## Behaviour both clients share

- **Request IDs.** `result.request_id` (`requestId` in JavaScript) is the ID in the `X-Request-ID` header and in the Request logs page. Errors carry it too. A call may set its own ID (8 to 64 letters, digits, dots, dashes or underscores) to correlate with your logs.
- **Retries.** At most two, only when repeating a call cannot do the work twice. A 429 waits for `Retry-After` (not if it is over 30 seconds). A 503 is retried unless waiting cannot help (`provider_not_configured`). A read is also retried on 502, 504, timeouts and connection failures. A write is not retried after it may have reached the model, but is if the connection could not be made.
- **Redirects are never followed**, so an API key is never sent to another address.
- Plain `http://` is accepted only for loopback unless you opt in.
- The key is never printed, logged or put in an error.
- A response over 8 MiB, or audio over 25 MiB, is refused.

## Tests

Both suites start a real HTTP server on loopback and run in `scripts\check.ps1` (skip them with `-SkipSdk`).

- Python: 29 tests, standard library `unittest`.
- JavaScript: 29 tests, `node:test`, after a strict TypeScript build.

They test the client against a fake server that follows the API reference. They do not talk to a live Forge deployment.
