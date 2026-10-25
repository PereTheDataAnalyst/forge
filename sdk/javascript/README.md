# natlas-forge (JavaScript and TypeScript)

A typed client for [N-ATLAS Forge](../../README.md), the hosted API for the N-ATLAS language model. It wraps the three gateway calls that use a project API key:

| Call | Needs the scope | What it does |
| --- | --- | --- |
| `forge.models.list()` | `models.read` | The models available through the gateway |
| `forge.chat.create({...})` | `chat.create` | Generate a reply in English, Hausa, Igbo or Yoruba |
| `forge.audio.transcribe({...})` | `asr.transcribe` | Transcribe a short audio clip (needs a speech worker on the deployment) |

It has no runtime dependencies. It uses the built-in `fetch`, so it runs on Node 18 or newer, Bun and Deno. It is ESM only and ships TypeScript declarations.

**Use it from a server.** An API key in browser code is public. Keep the key in an environment variable on your backend.

**Status.** This package is not published to npm. Build it from the repository (below). The version is `0.1.0` and the name `natlas-forge` is provisional.

## Install

From a checkout of the repository:

```
cd sdk/javascript
npm install
npm run build
npm pack                      # creates natlas-forge-0.1.0.tgz
```

then, in your own project:

```
npm install /path/to/natlas-forge-0.1.0.tgz
```

## Use

```ts
import { Forge } from "natlas-forge";

const forge = new Forge({ baseUrl: "https://your-forge-address", apiKey: process.env.FORGE_API_KEY! });

const reply = await forge.chat.create({
  messages: [{ role: "user", content: "Ẹ kú àárọ̀. Ṣe daadaa ni?" }],
  language: "yo", // recorded in your logs and charts, it does not change the model
  maxTokens: 200,
});
console.log(reply.text);
console.log(reply.requestId); // req_... the same ID the Request logs page shows
console.log(reply.usage.totalTokens, reply.latencyMs, reply.finishReason, reply.rateLimit.remaining);
```

`baseUrl` is the address of your Forge deployment. The client adds `/api/v1`. `Forge.fromEnv(process.env)` builds a client from `FORGE_API_KEY` and `FORGE_BASE_URL`.

### Speech

```ts
import { readFile } from "node:fs/promises";

const result = await forge.audio.transcribe({
  audio: await readFile("clip.wav"), // a Blob or File, an ArrayBuffer or a typed array
  language: "yo",
  contentType: "audio/wav",          // optional: defaults to the Blob's own type
});
console.log(result.text, result.model, result.requestId);
console.log(result.attribution); // show this wherever you show the transcript
```

The server accepts WAV, MP3, OGG, FLAC, MP4/M4A and WebM, up to 10 MiB and 30 seconds. If the deployment has no speech worker connected the call throws an error with `code === "provider_not_configured"`. Nothing in this package measures how accurate a transcript is.

## Request IDs

Every result has `requestId`, and every error thrown from a server response has `.requestId`. It is the ID in the `X-Request-ID` header and in the Request logs page. Quote it when you ask for help. You can also choose one per call (`{ requestId: "my-trace-0001" }`, 8 to 64 letters, digits, dots, dashes or underscores) to correlate your own logs.

## Errors

All errors extend `ForgeError` and carry `message`, `status`, `code`, `requestId`, `details` and `retryAfter`.

| Class | When |
| --- | --- |
| `ForgeAuthenticationError` | 401: the key is missing, wrong, revoked or expired |
| `ForgePermissionError` | 403: the key lacks the scope, or the project is archived |
| `ForgeNotFoundError` | 404, for example an unknown model |
| `ForgeInvalidRequestError` | 400, 413, 415, 422: fix the request. `details` lists fields for a 422 |
| `ForgeRateLimitError` | 429: `retryAfter` says how long to wait, in seconds |
| `ForgeServerError` | 5xx: the platform or the model host failed |
| `ForgeConnectionError`, `ForgeTimeoutError` | The server could not be reached, or did not answer in time |
| `ForgeAbortError` | Your `AbortSignal` ended the request |

```ts
import { ForgeError, ForgeRateLimitError } from "natlas-forge";

try {
  await forge.chat.create({ messages: [{ role: "user", content: "Hello" }] });
} catch (error) {
  if (error instanceof ForgeRateLimitError) console.log("wait", error.retryAfter, error.requestId);
  else if (error instanceof ForgeError) console.log(error.code, error.status, error.requestId);
  else throw error;
}
```

Pass `{ signal }` as the second argument of any call to cancel it, and `{ timeoutMs }` to change the timeout for one call.

## Retries

By default a failed call is retried at most twice (`maxRetries: 2`) with a short backoff, and only when repeating it cannot do the work twice:

- A 429 is retried after the delay the server gave, unless that delay is over 30 seconds.
- A 503 is retried, except codes that waiting cannot fix, such as `provider_not_configured`.
- A read (`models.list`) is also retried on 502, 504, timeouts and connection failures.
- A write (`chat.create`, `audio.transcribe`) is not retried after it may have reached the model: not on 502, 504, 500 or a timeout. It is retried if the connection could not be made at all.
- A caller abort and an oversized response are never retried.

Set `maxRetries: 0` to turn retries off.

## Safety

- **Redirects are never followed** (`redirect: "manual"`), so your API key is never sent to a different address. A redirect throws an error.
- Plain `http://` is accepted only for `localhost`, `127.0.0.1` and `[::1]`. Set `allowInsecureHttp: true` for a private network you control.
- The key is kept in a private class field. `String(forge)`, `JSON.stringify(forge)` and `console.log(forge)` never show it, and error messages never contain it.
- The key is checked for spaces and line breaks, so it cannot be used to inject a header.
- Responses over 8 MiB are refused. Audio over 25 MiB is refused before it is sent.
- To use a proxy, pass your own `fetch` in the options.

## Tests

```
cd sdk/javascript
npm install
npm test
```

The tests start a real HTTP server on loopback.
