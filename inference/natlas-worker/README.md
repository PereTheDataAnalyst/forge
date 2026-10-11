# N-ATLAS inference worker

Runs the official `NCAIR1/N-ATLaS` model with Transformers, exactly as the model card shows (chat template rendered with `date_string`, then `generate`), and exposes it to the Forge gateway through a small authenticated JSON API. Keep it on a private address.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# 1. Install PyTorch for your hardware: https://pytorch.org/get-started/locally/
# 2. Then the model libraries:
pip install -r requirements-model.txt
Copy-Item .env.example .env       # set NATLAS_WORKER_TOKEN and HF_TOKEN
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8081
```

The model is gated: accept the terms on its Hugging Face page and use a read token. Loading takes minutes and `GET /v1/info` reports `state: loading` until it is ready. `scripts\setup-local.ps1 -WithWorker` and `scripts\connect-worker.ps1` automate the token and the gateway settings.

## API

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /healthz` | none | Liveness only |
| `GET /v1/info` | bearer | State (`loading`, `ready`, `failed`), model id, device, dtype |
| `POST /v1/chat` | bearer | `{ messages, max_new_tokens, temperature, top_p, repetition_penalty }` returns `{ text, finish_reason, prompt_tokens, completion_tokens, model_id, generation_ms }` |

Errors: `{ "error": { "code", "message" } }` with codes `unauthorized`, `invalid_request`, `model_loading`, `model_failed`, `context_length_exceeded`, `busy`, `generation_timeout`, `generation_failed`, `payload_too_large`.

## Guarantees

- Never returns text the model did not generate. If the model is not ready it says so.
- Never logs prompts or generated text.
- Serialises generation and bounds the queue (`NATLAS_WORKER_MAX_CONCURRENCY`, `NATLAS_WORKER_MAX_QUEUE`).
- Enforces `prompt tokens + max_new_tokens <= NATLAS_WORKER_CONTEXT_LIMIT_TOKENS` with exact token counts.

Tests (no model needed): `pip install -r requirements-dev.txt` then `python -m pytest -q`.
