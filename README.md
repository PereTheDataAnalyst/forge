# N-ATLAS Forge

Developer infrastructure for building with N-ATLAS. Built by StackTechnology for the National AI Innovation Challenge 2026 (Developer Infrastructure).

**Live demo: <https://natlas-forge-18d307.prostackng.com.ng>**

N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and Digital Economy, and powered by Awarri Technologies. N-ATLAS Forge is an independent project and is not endorsed by these organisations.

> **Status: foundation build, deployed.** [docs/status.md](docs/status.md) states exactly what exists and what has been verified. Nothing in this repository returns invented model output.

## Try the live demo

1. Open <https://natlas-forge-18d307.prostackng.com.ng> and choose **Create account**. There is no email verification yet, so any email address works.
2. Create a project, then create an API key. The full key is shown once, so copy it.
3. Send a request to the gateway.

   ```bash
   curl -X POST https://natlas-forge-18d307.prostackng.com.ng/api/v1/chat \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":64}'
   ```

   The same request in Windows PowerShell:

   ```powershell
   $env:FORGE_API_KEY = "YOUR_API_KEY"
   $body = '{"messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":64}'
   Invoke-RestMethod -Method Post -Uri https://natlas-forge-18d307.prostackng.com.ng/api/v1/chat `
     -Headers @{ Authorization = "Bearer $env:FORGE_API_KEY" } `
     -ContentType "application/json; charset=utf-8" -Body $body
   ```

4. Open the **Dashboard**. The request appears with its status, latency and token counts. Prompts and responses are not stored.

The model runs on a separate GPU host that is not always on. The **N-ATLAS inference** card on the dashboard shows whether it is connected right now. When it is not, chat requests return a structured `provider_unavailable` error and everything else keeps working. The in-app **Documentation** page covers authentication, endpoints, errors and rate limits. What has been run, by whom and when is recorded in [docs/status.md](docs/status.md).

## What it provides today

| Capability | State |
| --- | --- |
| API gateway: API key authentication with scopes, rate limits, validation, request IDs, structured errors | Implemented |
| Accounts, projects and API keys (shown once, stored as keyed hashes, revoke and rotate) | Implemented |
| Dashboard computed from real request data, request logs and usage counters in the database | Implemented |
| Website, interactive request builder and documentation | Implemented |
| Production container that serves the API and the web app, deployed on a container platform | Implemented. The smoke test against the live address passed on 14 October 2026, see [docs/status.md](docs/status.md). |
| N-ATLAS inference worker wrapping the official Transformers usage | Implemented. Run against the real model on a GPU notebook on 12 October 2026, see [docs/status.md](docs/status.md). It is not hosted persistently yet. |
| Playground, SDKs, request log viewer, metrics charts | Not built yet |
| Evaluation, speech recognition and MCP | Engines are in `integrations/` as a standalone library. They are not wired into the platform yet, so they are not available in the product. |

## Quick start (Windows, PowerShell)

```powershell
cd C:\Projects\forge
.\scripts\preflight.ps1      # read-only report of tools, services and Git state
.\scripts\setup-local.ps1    # .env, databases, virtual environment, migrations, npm install
.\scripts\dev.ps1            # backend on 8000, frontend on 5173
.\scripts\smoke-day1.ps1     # exercises the real flow end to end
.\scripts\check.ps1          # lint, migration parity, tests, type check, production build
```

Open <http://localhost:5173>. PostgreSQL and Redis (Memurai) must already be running. See [docs/local-development.md](docs/local-development.md).

## Connecting N-ATLAS

The official release is open weights on Hugging Face (`NCAIR1/N-ATLaS`, gated). There is no hosted public endpoint, so Forge ships an inference worker. Until it is connected, chat requests return a structured `provider_not_configured` error. Details: [docs/n-atlas-integration.md](docs/n-atlas-integration.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `backend/` | FastAPI, SQLAlchemy, Alembic, tests |
| `frontend/` | React, TypeScript, Vite |
| `inference/natlas-worker/` | Runs the model behind an authenticated API |
| `integrations/` | Evaluation Lab engine, voice pipeline and speech worker, MCP server (standalone library, see its README) |
| `Dockerfile`, `docker/` | Production image that serves the API and the web app |
| `scripts/` | PowerShell tooling |
| `tools/checks/` | Dependency-free verification tools |
| `docs/` | Documentation |

## Documentation

[Architecture](docs/architecture.md), [Local development](docs/local-development.md), [Environment variables](docs/environment-variables.md), [API reference](docs/api-reference.md), [N-ATLAS integration](docs/n-atlas-integration.md), [Security](docs/security.md), [Licensing and attribution](docs/licensing-and-attribution.md), [Extension modules](docs/extension-modules.md), [Branching and handoff](docs/branching-and-handoff.md), [Deployment](docs/deployment.md), [Model hosting](docs/model-hosting.md), [Troubleshooting](docs/troubleshooting.md), [Status](docs/status.md).

## N-ATLAS terms and licence

Use of N-ATLAS is governed by the [N-ATLaS Terms of Use](https://huggingface.co/NCAIR1/N-ATLaS), including required attribution and a limit of 1,000 active end users before a commercial licence is needed. See [ATTRIBUTION.md](ATTRIBUTION.md).

No licence has been chosen for this repository yet. Until a `LICENSE` file is added, all rights are reserved by StackTechnology.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
