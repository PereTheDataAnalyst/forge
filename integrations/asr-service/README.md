# N-ATLAS ASR worker

A small HTTP service that runs the four official N-ATLAS speech models (Hausa, Igbo, Yoruba, Nigerian-accented English) so the platform and the MCP
server can transcribe audio. Design and endpoints: `../docs/voice.md`. Model facts: `../docs/n-atlas-verified-facts.md`.

**Status: the service is tested end to end with a test engine in place of the model. Real model inference has not been run yet.** The steps below,
ending with `selftest`, are how you prove it on your own machine. Until `selftest` has printed a transcript, do not describe voice transcription as working.

## What you need

- Windows with PowerShell, Python 3.10 to 3.12 (use the version PyTorch supports for your machine), and `ffmpeg` on `PATH` (`ffmpeg -version` must work).
- A Hugging Face account that has accepted the conditions on each model page you will use (the repositories are gated):
  <https://huggingface.co/NCAIR1/Hausa-ASR>, <https://huggingface.co/NCAIR1/Igbo-ASR>, <https://huggingface.co/NCAIR1/Yoruba-ASR>,
  <https://huggingface.co/NCAIR1/NigerianAccentedEnglish>
- A Hugging Face access token for that account (read access). Keep it in an environment variable, never in a file you commit.
- Disk space and time for the downloads (each model is a Whisper Small, 244M parameters). A GPU is optional: `ASR_DEVICE=cpu` works, more slowly.

## 1. Own virtual environment

Do not install this into the same environment as the MCP server or the platform: it brings `torch` and `transformers`.

```powershell
cd C:\Projects\forge\integrations
py -3 -m venv .venv-asr
.\.venv-asr\Scripts\Activate.ps1
python -m pip install --upgrade pip
# Install PyTorch first, using the command PyTorch's own selector gives for your machine (CPU or CUDA): https://pytorch.org/get-started/locally/
python -m pip install -e ".[asr]"
```

## 2. Configure

```powershell
# a worker token the platform will present (24+ characters). Create one and store it in your secret manager:
$env:ASR_SERVICE_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:HF_TOKEN = "<your Hugging Face access token>"
$env:ASR_DEVICE = "auto"                 # or cpu / cuda
$env:ASR_PRELOAD_LANGUAGES = "yo"        # optional: load these at start
python -m forge_integrations.asr_service check
```

`check` prints `ok` or `NO` for configuration, authentication, ffmpeg, whether `torch`/`transformers`/`numpy` can be imported, and whether `HF_TOKEN` is present (presence only; the value is never printed). It exits non-zero until ffmpeg and the runtime are in place.

## 3. Prove the real models work (selftest)

Use a short clip (30 seconds at most) of someone speaking the language you pass. `selftest` needs the same configuration as the service; for a quick local run without a token:

```powershell
$env:ASR_ALLOW_UNAUTHENTICATED_LOOPBACK = "true"   # local test only, loopback host only
python -m forge_integrations.asr_service selftest --language yo --audio C:\path\to\clip.wav
```

The first run downloads the model, so it takes a while. It prints the model repository, the audio length, the elapsed time (including model load) and the text.
Read the text with a speaker of that language before you trust the setup, and note the result honestly in the project notes.

## 4. Run the worker

```powershell
Remove-Item Env:ASR_ALLOW_UNAUTHENTICATED_LOOPBACK -ErrorAction SilentlyContinue
python -m forge_integrations.asr_service serve
# listening on 127.0.0.1:8091 by default
```

Check it:

```powershell
Invoke-RestMethod http://127.0.0.1:8091/healthz
Invoke-RestMethod http://127.0.0.1:8091/v1/models -Headers @{ Authorization = "Bearer $env:ASR_SERVICE_TOKEN" }
```

Keep the worker on a private address. For any non-loopback use put TLS in front of it (a reverse proxy) and point the platform at the `https` address.

## 5. Point the platform or the MCP server at it

On the machine running the MCP server (or the platform):

```powershell
$env:ASR_SERVICE_URL   = "http://127.0.0.1:8091"
$env:ASR_SERVICE_TOKEN = "<the same worker token>"
$env:ALLOW_PRIVATE_NETWORK_TARGETS = "true"    # only while the worker is on loopback or a private address
$env:ALLOW_INSECURE_HTTP_TARGETS   = "true"    # only while there is no TLS between them
```

With a TLS address and a public or allow-listed host, leave both `ALLOW_...` variables off. The `transcribe` MCP tool appears once `ASR_SERVICE_URL` is set.

Try the example:

```powershell
python ..\examples\voice\transcribe_file.py C:\path\to\clip.wav --language yo
```

## Settings

See the table in `../docs/voice.md`. A starting template is in `.env.example` (variable names only, no values).

## Troubleshooting

| Message | Meaning and fix |
| --- | --- |
| `ASR_SERVICE_TOKEN is required` | Set a token of 24 or more characters, or use the loopback development switch for a local test. |
| `NO ffmpeg` | Install ffmpeg and make sure `ffmpeg -version` works in the same PowerShell window, or set `FFMPEG_PATH`. |
| `NO model_runtime_importable` | Install PyTorch first, then `pip install -e ".[asr]"` inside `.venv-asr`. |
| `provider_unavailable` mentioning the model | The model could not be loaded: check `HF_TOKEN`, that the account accepted the model's conditions, and that the machine can reach huggingface.co. |
| `audio_too_long` | The models take 30 seconds at most per inference. Cut the clip. |
| Slow first request | The model loads on first use. Set `ASR_PRELOAD_LANGUAGES`. |
