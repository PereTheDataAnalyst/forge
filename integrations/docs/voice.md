# Voice: audio checks, the ASR worker and the pipeline

Voice has three parts: strict audio validation, the ASR worker that runs the four official N-ATLAS speech models, and a small pipeline that
ties validation, transcription and (optionally) a model reply together with a recorded step trail.

Facts about the models (four separate Whisper Small models, 16 kHz, 30 seconds, gated) are in `n-atlas-verified-facts.md`. Forge does not
detect the language: the caller states `ha`, `ig`, `yo`, or `en` (Nigerian-accented English) and the matching model runs. Anything else is refused with `language_unsupported`.

## Audio validation (`voice/audio.py`)

Nothing is trusted from the client. In order:

1. Empty audio is refused (`audio_empty`); over the size limit is refused (`audio_too_large`, default 10 MiB, `VOICE_MAX_UPLOAD_BYTES`).
2. The format is decided by **file signature**, never by file name: WAV, MP3, OGG, FLAC, MP4/M4A, WebM. Anything else is `audio_unsupported_type`.
3. A declared content type must agree with the signature (`audio_signature_mismatch`). Generic values such as `application/octet-stream` are accepted because browsers send them.
4. For WAV the duration is computed from the header and over 30 seconds is refused (`audio_too_long`, `VOICE_MAX_SECONDS`, at most 30 because the models take 30 seconds per inference). A malformed WAV is `audio_corrupt`.
5. Compressed formats are decoded by the worker, which enforces the duration there.

The result records the format, size, SHA-256 and duration. Audio bytes are never logged or placed in audit records; only sizes and codes are.

## Decoding (`voice/transcode.py`)

Audio is converted to 16 kHz mono 16-bit PCM by an `ffmpeg` subprocess, hardened as follows: a fixed argument list (never a shell string) with a generated input path;
the demuxer forced from the sniffed format so ffmpeg never auto-probes into playlist or network inputs; `-protocol_whitelist file`; video, subtitle and data streams
dropped; input duration, output duration and output size capped; a wall-clock timeout that kills the process. ffmpeg must be installed on the worker's machine (`FFMPEG_PATH` if it is not on `PATH`).

Uploads are written under random names with a fixed suffix allow-list, mode 0600 on POSIX, and removed when the request ends, including on errors. Stale files are swept.

## The ASR worker (`python -m forge_integrations.asr_service`)

A small HTTP service that loads the official models on demand and transcribes. It is a separate process because it needs `torch` and `transformers`,
and because heavy inference should not share a process with the API.

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /healthz`, `GET /readyz` | none | Liveness and readiness, no detail. |
| `GET /v1/models` | Bearer | Verified model facts and worker status. |
| `POST /v1/audio/transcriptions` | Bearer | `multipart/form-data` with `file` and `language`. Returns `{text, language, model, duration_seconds, request_id}`; errors use `{"error": {"code", "message", "request_id"}}`. |

This is Forge's own service contract. It is not an official N-ATLAS API.

Request flow: authenticate **before** reading the body; bound the body (`ASR_MAX_UPLOAD_BYTES` plus 64 KiB of multipart framing); parse one file and a few small fields;
validate by signature; decode in the hardened subprocess; run the model in a worker thread under a timeout; return text. Work is admitted through a bounded gate:
`ASR_MAX_CONCURRENCY` (default 1) jobs run, `ASR_MAX_WAITING` (default 8) may queue, everything beyond is refused at once with a retryable error. Authentication compares
SHA-256 digests in constant time. Audio and transcripts are never logged.

Commands:

```powershell
python -m forge_integrations.asr_service check      # configuration, ffmpeg, runtime importable, HF_TOKEN present (presence only, never printed)
python -m forge_integrations.asr_service serve      # start the worker
python -m forge_integrations.asr_service selftest --language yo --audio sample.wav   # one real transcription, locally
```

Setup is in `asr-service/README.md` (its own virtual environment, the gated-model token, GPU or CPU). Settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASR_SERVICE_TOKEN` | none | Required, at least 24 characters (anonymous access only on loopback with `ASR_ALLOW_UNAUTHENTICATED_LOOPBACK=true`, for development). |
| `ASR_HOST`, `ASR_PORT` | `127.0.0.1`, `8091` | Where to listen. Keep it private; put TLS in front for anything but loopback. |
| `ASR_MAX_UPLOAD_BYTES`, `ASR_MAX_SECONDS` | 10 MiB, 30 | Upload bounds. |
| `ASR_DECODE_TIMEOUT_SECONDS`, `ASR_INFERENCE_TIMEOUT_SECONDS` | 30, 120 | Time bounds. |
| `ASR_MAX_CONCURRENCY`, `ASR_MAX_WAITING` | 1, 8 | Admission control. |
| `ASR_DEVICE` | `auto` | `auto`, `cpu` or `cuda`. |
| `ASR_PRELOAD_LANGUAGES` | none | For example `yo,ha`. Loads those models at start so the first request is not slow. |
| `ASR_PROCESSOR_OVERRIDES` | none | `ha=owner/repository`. See the Hausa note in the facts page. |
| `ASR_TEMP_DIR`, `FFMPEG_PATH` | system temp, `ffmpeg` | Working directory and binary. |
| `HF_TOKEN` | none | Hugging Face token of an account that accepted each model's conditions. Read by the Hugging Face libraries, never by Forge. |

## The client side (`voice/asr_client.py`)

`HttpAsrProvider` implements the `AsrProvider` port against that contract. It only calls hosts an operator allow-listed (`ASR_ALLOWED_HOSTS`, defaulting to the host of
`ASR_SERVICE_URL`). User input never forms a URL. The host is re-resolved and re-validated before every call, link-local and metadata addresses are always refused, private or
loopback addresses only with `ALLOW_PRIVATE_NETWORK_TARGETS=true`, plain HTTP only with `ALLOW_INSECURE_HTTP_TARGETS=true`, redirects are never followed, the connection is pinned to the
validated address, and the reply size is bounded. The token travels only in the `Authorization` header.

## The pipeline (`voice/pipeline.py`)

```python
from forge_integrations.voice import HttpAsrProvider, VoicePipeline

pipeline = VoicePipeline(asr_provider, gateway=model_gateway)           # gateway is optional
result = await pipeline.transcribe(audio_bytes, language="yo", declared_mime="audio/wav")
reply  = await pipeline.transcribe_and_respond(audio_bytes, language="yo", respond_language="yo")
print(result.to_dict())   # request id, audio facts, transcript, optional reply, and the step trail
```

Every run records steps (`validate`, `asr`, `llm`) with status, duration and error code, so the voice playground can show where time went and where it failed. A failure raises
`VoiceFailure`, a `ForgeError` that carries the recorded steps. Without a gateway, `transcribe_and_respond` answers `provider_not_configured` instead of pretending.

Example: `examples/voice/transcribe_file.py`.

## Tested and not tested

- Tested: validation for every format and every refusal; the hardened decode with real ffmpeg output; the worker application end to end with a test engine in place of the model (authentication, limits, temp-file cleanup, admission control); the client's outbound protections including pinned connections and TLS; the pipeline; the example script against the real worker application.
- Skipped when ffmpeg is missing: the tests that decode audio. The rest still run.
- **Not tested: real model inference.** Running the four official models needs `torch`, `transformers`, a Hugging Face token for the gated repositories, and downloads of several hundred megabytes per model. It could not be done in the build sandbox. The `selftest` command exists to prove it on the machine that will run the worker. Until it has been run there, do not describe voice transcription as working.
- No transcription accuracy or word error rate has been measured, and none should be quoted.
