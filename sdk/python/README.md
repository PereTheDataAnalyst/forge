# natlas-forge (Python)

A typed Python client for [N-ATLAS Forge](../../README.md), the hosted API for the N-ATLAS language model. It wraps the three gateway calls that use a project API key:

| Call | Needs the scope | What it does |
| --- | --- | --- |
| `forge.models.list()` | `models.read` | The models available through the gateway |
| `forge.chat.create(...)` | `chat.create` | Generate a reply in English, Hausa, Igbo or Yoruba |
| `forge.audio.transcribe(...)` | `asr.transcribe` | Transcribe a short audio clip (needs a speech worker on the deployment) |

It uses only the Python standard library, supports Python 3.9 and newer, and ships type information (`py.typed`).

**Status.** This package is not published to PyPI. Install it from the repository (below). The version is `0.1.0` and the package name `natlas-forge` is provisional.

## Install

From a checkout of the repository:

```
python -m pip install ./sdk/python
```

Straight from GitHub, without a checkout:

```
python -m pip install "natlas-forge @ git+https://github.com/PereTheDataAnalyst/forge.git#subdirectory=sdk/python"
```

## Use

```python
from natlas_forge import Forge

forge = Forge(base_url="https://your-forge-address", api_key="nfk_...")

reply = forge.chat.create(
    [{"role": "user", "content": "Ẹ kú àárọ̀. Ṣe daadaa ni?"}],
    language="yo",       # recorded in your logs and charts, it does not change the model
    max_tokens=200,
)
print(reply.text)
print(reply.request_id)          # req_... the same ID the Request logs page shows
print(reply.usage.total_tokens, reply.latency_ms, reply.finish_reason)
print(reply.rate_limit.remaining)
```

`base_url` is the address of your Forge deployment. The client adds `/api/v1`. Both values can come from the environment instead: `FORGE_BASE_URL` and `FORGE_API_KEY`.

### Speech

```python
result = forge.audio.transcribe("clip.wav", language="yo")   # a path, bytes or a binary file object
print(result.text, result.model, result.request_id)
print(result.attribution)   # show this wherever you show the transcript
```

The server accepts WAV, MP3, OGG, FLAC, MP4/M4A and WebM, up to 10 MiB and 30 seconds. If the deployment has no speech worker connected the call raises an error with `code == "provider_not_configured"`. Nothing in this package measures how accurate a transcript is.

### Async

```python
from natlas_forge import AsyncForge

forge = AsyncForge(base_url="https://your-forge-address", api_key="nfk_...")
reply = await forge.chat.create([{"role": "user", "content": "Hello"}])
```

`AsyncForge` runs the blocking client on worker threads, so awaiting it never blocks your event loop. It is not a native non-blocking client.

## Request IDs

Every result has `request_id`, and every error raised from a server response has `.request_id`. It is the ID in the `X-Request-ID` header and in the Request logs page. Quote it when you ask for help. You can also choose one per call (`request_id="my-trace-0001"`, 8 to 64 letters, digits, dots, dashes or underscores) to correlate your own logs.

## Errors

All errors derive from `ForgeError` and carry `message`, `status`, `code`, `request_id`, `details` and `retry_after`.

| Class | When |
| --- | --- |
| `AuthenticationError` | 401: the key is missing, wrong, revoked or expired |
| `PermissionDeniedError` | 403: the key lacks the scope, or the project is archived |
| `NotFoundError` | 404, for example an unknown model |
| `InvalidRequestError` | 400, 413, 415, 422: fix the request. `details` lists fields for a 422 |
| `RateLimitError` | 429: `retry_after` says how long to wait |
| `ServerError` | 5xx: the platform or the model host failed |
| `APIConnectionError`, `APITimeoutError` | The server could not be reached, or did not answer in time |

```python
from natlas_forge import RateLimitError, ForgeError

try:
    forge.chat.create([{"role": "user", "content": "Hello"}])
except RateLimitError as error:
    print("slow down for", error.retry_after, "seconds", error.request_id)
except ForgeError as error:
    print(error.code, error.status, error.request_id)
```

## Retries

By default a failed call is retried at most twice (`max_retries=2`) with a short backoff, and only when repeating it cannot do the work twice:

- A 429 is retried after the delay the server gave, unless that delay is over 30 seconds.
- A 503 is retried, except codes that waiting cannot fix, such as `provider_not_configured`.
- A read (`models.list`) is also retried on 502, 504, timeouts and connection failures.
- A write (`chat.create`, `audio.transcribe`) is not retried after it may have reached the model: not on 502, 504, 500 or a read timeout. It is retried if the connection could not be made at all.

Set `max_retries=0` to turn retries off.

## Safety

- **Redirects are never followed**, so your API key is never sent to a different address. A redirect raises an error.
- Plain `http://` is accepted only for `localhost`, `127.0.0.1` and `::1`. Pass `allow_insecure_http=True` for a private network you control.
- The key is never printed: `repr(forge)` shows only its first eight characters, and error messages never contain it.
- The key is checked for spaces and line breaks, so it cannot be used to inject a header.
- The standard library transport ignores proxy environment variables. To use a proxy, pass your own `transport=` function. It receives `(method, url, headers, body, timeout)` and returns `natlas_forge._transport.RawResponse`.
- Responses over 8 MiB are refused.

## Tests

```
python -m unittest discover -s sdk/python/tests
```

with `PYTHONPATH=sdk/python/src`. The tests start a real HTTP server on loopback.
