# Voice

Voice transcribes a short audio clip with the official N-ATLAS speech recognition models. The platform checks the audio, calls a separate **speech worker**, and records metadata only. Audio and transcripts are never stored, logged or put in an error message.

**Status.** The platform side is built and unit tested. **No speech worker has been run against it**, so nothing here shows that a transcript is correct or even that a real worker answers. Until a worker is connected every transcription returns `503 provider_not_configured`, and says so. The Voice page in the web app is not built yet; the endpoints below are.

## How it fits together

```
browser or your app --> platform (/api/v1) --> speech worker (separate host) --> model
                           |
                           +--> requests and voice_requests: metadata only
```

The worker is `integrations/asr-service`. The official models are open weights that need `torch`, a Hugging Face token (the models are gated) and in practice a GPU, so they cannot run inside the platform image. Setting it up is covered in [integrations/docs/voice.md](../integrations/docs/voice.md) and [integrations/asr-service/README.md](../integrations/asr-service/README.md). Model facts and the attribution text each model requires come from [integrations/docs/n-atlas-verified-facts.md](../integrations/docs/n-atlas-verified-facts.md), verified on 9 October 2026.

| Language | Code | Model |
| --- | --- | --- |
| Nigerian accented English | `en` | `NCAIR1/NigerianAccentedEnglish` |
| Hausa | `ha` | `NCAIR1/Hausa-ASR` |
| Igbo | `ig` | `NCAIR1/Igbo-ASR` |
| Yoruba | `yo` | `NCAIR1/Yoruba-ASR` |

## Endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /audio/transcriptions?language=yo` | API key with the `asr.transcribe` scope | The developer API |
| `POST /voice/transcriptions?project_id=...&language=yo` | Browser session and CSRF header | For the Voice page |
| `GET /voice/status` | Browser session | Whether a worker is configured, the limits, and the model and attribution for each language |
| `GET /projects/{id}/voice/requests?limit=20` | Browser session | Recent requests of a project, metadata only |

### Sending audio

The request body **is the audio file**, sent raw. Do not send a form or JSON: that is a 415 `unsupported_media_type`. Set `Content-Type` to the file's type, for example `audio/wav`. `application/octet-stream` and no type at all are accepted, and the format is then read from the file itself. A type that disagrees with the content is refused (`audio_signature_mismatch`).

```
curl -X POST "https://your-forge-address/api/v1/audio/transcriptions?language=yo" \
  -H "Authorization: Bearer $FORGE_API_KEY" \
  -H "Content-Type: audio/wav" \
  --data-binary @clip.wav
```

The answer:

```json
{
  "request_id": "req_3f9a...",
  "language": "yo",
  "model": "NCAIR1/Yoruba-ASR",
  "text": "...",
  "audio": { "format": "wav", "size_bytes": 32044, "duration_seconds": 1.0 },
  "latency_ms": 910,
  "attribution": "..."
}
```

Show `attribution` wherever you show the transcript. The browser endpoint adds `steps`, the time spent validating and in the worker. Rate limit headers are on every response. The typed SDKs have `audio.transcribe`, see [sdks.md](sdks.md).

### Limits

| Limit | Value | Setting |
| --- | --- | --- |
| File size | 10 MiB | `VOICE_MAX_UPLOAD_BYTES` |
| Length | 30 seconds | `VOICE_MAX_SECONDS` (at most 30) |
| Formats | WAV, MP3, OGG, FLAC, MP4/M4A, WebM | fixed |
| Requests per minute | 10 per API key, or per person in the browser | `VOICE_RATE_LIMIT_PER_MINUTE` |
| Uploads in flight | 4 per process | `VOICE_MAX_CONCURRENT` |

The length of a WAV file is read from its header and enforced here. For compressed formats the platform cannot know the length without decoding, so the worker enforces it.

### Errors

| Status | `code` | Meaning |
| --- | --- | --- |
| 401 | `missing_api_key`, `invalid_api_key`, `api_key_revoked`, `api_key_expired` | Authentication |
| 403 | `insufficient_scope` | The key lacks `asr.transcribe` |
| 413 | `audio_too_large`, `payload_too_large` | Over the size limit. `payload_too_large` is a body far beyond it |
| 415 | `unsupported_media_type`, `audio_unsupported_type`, `audio_signature_mismatch` | Not a supported audio file |
| 422 | `audio_too_long`, `audio_empty`, `audio_corrupt`, `language_unsupported`, `validation_error` | The clip is too long, empty or damaged, or the request is not usable |
| 429 | `rate_limited` | Wait for `Retry-After` |
| 503 | `provider_not_configured` | No speech worker is connected to this deployment |
| 503 | `provider_unavailable`, `provider_busy` | The worker cannot be reached, or the platform is handling its limit of uploads. Retry later |
| 504 | `provider_timeout` | The worker did not answer in time |

Every error carries `request_id`. A request that was refused for its language or by the rate limit before any work started has no database row. Every other outcome does, including failures, so the request logs show what was attempted.

## What is stored

For each admitted request, one row in `requests` (shown in the Request logs page with source `voice`) and one in `voice_requests`: request ID, project, key or person, language, model, **format** (`wav`, `mp3` and so on, never text sent by a client), audio size and duration, status, validation and worker timings, the number of characters in the transcript, and an error code. The audio and the transcript are not stored anywhere.

## Connecting a worker

Set `ASR_SERVICE_URL` and `ASR_SERVICE_TOKEN` together in the platform's environment. The host in the URL is always allowed. Rules the platform enforces on the address:

- A worker on a **loopback or private network address** is refused unless `ASR_ALLOW_PRIVATE_NETWORK=true`. Set it for a worker on your own machine or network.
- Plain `http://` to a public host is refused. In production a non-loopback `http://` URL needs `ASR_ALLOW_PLAINTEXT_REMOTE=true`, for a private network only.
- The platform starts without a worker, and without reaching one. The client for the worker is built on the first request, so a worker that is down at start-up does not stop the platform, and Voice recovers once the worker can be reached.

`GET /voice/status` answers `not_configured`, `misconfigured` or `ready`. `ready` means a worker is configured and its address is usable. It does not mean the worker is up: that is only learned by transcribing. After connecting a worker, run `scripts\smoke-day1.ps1`; it reports `Speech recognition: VERIFIED` only when a worker answered.

## Recording in the browser

The Voice page will record with the browser's `MediaRecorder`. The platform is ready for it:

- `Permissions-Policy` allows the microphone for the page itself (`microphone=(self)`) and nothing else.
- The page's Content Security Policy has `media-src 'self' blob:`, so a recording can be played back before it is sent. It is the only change made to the policy for Voice. Scripts, styles and connections are as strict as before.
- Recording needs a secure context: https, or localhost.

## Verification

- The orchestration (validation, limits, error mapping, what is recorded, concurrency, a failing recorder) is tested without a database or a worker in `backend/tests/test_voice_service.py` and `test_voice_runtime.py`, with a real generated WAV file and a fake worker.
- The routes, authentication, CSRF, scopes, body limit, rate limit and the stored rows are covered by `backend/tests/test_voice_api.py`: integration tests that need PostgreSQL and Redis, written without a database to run them against.
- Nothing has been run against a real worker. Transcription quality for Hausa, Igbo, Yoruba and Nigerian English has not been measured by Forge.
