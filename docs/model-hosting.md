# Hosting the N-ATLAS worker

The gateway never loads the model itself. It calls an inference worker over HTTPS with a bearer token (`NATLAS_INFERENCE_URL` and `NATLAS_INFERENCE_TOKEN`). The worker in [`inference/natlas-worker`](../inference/natlas-worker) loads the official `NCAIR1/N-ATLaS` weights. Where that worker runs is a deployment decision, and it decides how reliable the model is for users.

## Options

| Where the worker runs | State | What to expect |
| --- | --- | --- |
| A Kaggle notebook ([gpu-host-notebook.md](gpu-host-notebook.md)) | Used for the first real verification of the gateway | Temporary. See the limits below. |
| A GPU machine that stays on | Not set up yet | The right choice for real users. Run the worker there with `NATLAS_WORKER_TOKEN` set and expose it over HTTPS. About 16 GB of GPU memory is needed for the 16-bit weights alone. |
| A quantised copy on CPU | Not evaluated | Nothing in this repository claims it. It would be a different artefact from the official weights, so its output quality must be measured before anyone relies on it. |

## Kaggle limits to plan around

- A session ends at Kaggle's maximum session length and counts against a weekly GPU quota. Check Kaggle's notebook documentation for the current numbers instead of relying on this page.
- The temporary tunnel address changes every time the notebook starts, so each new session needs the new address in `NATLAS_INFERENCE_URL`.
- Stop the session yourself when you are finished. Closing the browser tab does not necessarily stop it, and a running session keeps using quota.

Do not point production traffic at a notebook.

## What users see when the worker is away

The gateway never invents text. With the worker stopped or unreachable, chat requests return a structured error such as `provider_unavailable`, `/api/v1/health/ready` reports the provider as not `ok`, and the dashboard shows the model status. Nothing else in the platform depends on the worker, so accounts, projects, API keys and request logs keep working.

## Changing the worker address

1. Start the worker and note its HTTPS address.
2. Local development: `.\scripts\connect-worker.ps1 -Url https://NEW-ADDRESS`, then restart the backend.
3. Deployment: update `NATLAS_INFERENCE_URL` in the platform's environment settings and redeploy or restart. The token stays the same as long as the worker keeps the same `NATLAS_WORKER_TOKEN`.
4. Check `GET /api/v1/health/ready`. The provider shows `ok` when the model has finished loading.
