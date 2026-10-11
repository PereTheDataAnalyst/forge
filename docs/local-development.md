# Local development

The project is developed on Windows with PowerShell and VS Code. PostgreSQL and Redis (Memurai) are expected to be installed already. The scripts never reinstall them and never drop a database.

## Prerequisites

| Tool | Version | Check |
| --- | --- | --- |
| Git | any recent | `git --version` |
| Node.js | 20 or newer | `node --version` |
| Python | 3.11 or newer | `python --version` |
| PostgreSQL | 13 or newer, `psql` reachable | `psql --version` |
| Redis or Memurai | running on `localhost:6379` | `.\scripts\preflight.ps1` |

## First run

```powershell
cd C:\Projects\forge
.\scripts\preflight.ps1        # read-only report of tools, services and Git state
.\scripts\setup-local.ps1      # .env, databases, virtual environment, migrations, npm install
.\scripts\dev.ps1              # backend on 8000, frontend on 5173
.\scripts\smoke-day1.ps1       # in another window: exercises the real flow end to end
```

`setup-local.ps1` asks once for the PostgreSQL administrator password, only to create the `forge_app` role and the `forge_dev` and `forge_test` databases. The password is not stored.

Open `http://localhost:5173`. Use `localhost`, not `127.0.0.1`, for the web app so the Origin check matches `PUBLIC_BASE_URL`.

## Quality gates

```powershell
.\scripts\check.ps1            # ruff, alembic check, pytest, type check, lint, production build
.\scripts\check.ps1 -WithAudit # also runs npm audit
```

Integration tests use `forge_test` and Redis logical database 15. They refuse to run against a database whose name lacks `test`.

## Manual commands

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload

cd ..\frontend
npm run dev
npm run typecheck
npm run build
```

## Connecting N-ATLAS

See [n-atlas-integration.md](n-atlas-integration.md). Without a connected worker the gateway answers chat requests with a structured `provider_not_configured` error. That is intended behaviour.

## Adding a database change

Create a new Alembic revision after `0002` and keep the model definition in `app/db/models` consistent with it. `alembic check` must report no differences. Never edit an applied revision.
