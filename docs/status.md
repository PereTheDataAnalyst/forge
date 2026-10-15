# Status

This page separates what is implemented from what has been verified, and by whom. It is the honest answer to "does it work".

## Evidence recorded for the foundation package

| Check | Result |
| --- | --- |
| Pure-logic unit tests (redaction, client IP, Origin handling, API key and CSRF primitives, password policy, scopes) | 23 passed in the build sandbox |
| Static analysis of all backend and worker Python files (undefined names, first-party imports) | No findings. Seeded-bug canaries are detected. |
| ORM models against Alembic migrations (14 tables, 145 columns, 27 foreign keys, 15 indexes, constraint names) | No drift. Seeded drift is detected. |
| Frontend syntax (38 files) and strict type check | No errors against stand-in React, Router and Vite declarations. Seeded type errors are detected. |
| Landing, docs, security, privacy and terms pages rendered with React 19 | Render without runtime errors |
| Generated cURL, JavaScript and Python samples, run against stubbed network layers | The prompt arrives unchanged despite quotes, backslashes, `$`, backticks, newlines and diacritics |
| PowerShell scripts | Statically validated only (ASCII, balanced delimiters, no PowerShell 7 only syntax) |
| Copy lint (em dashes, banned phrases, emoji, obsolete company name) | Clean |

## Verified on the owner's machine

Recorded on 11 October 2026 from the owner's own command output (Windows 11, 4 GB RAM, PostgreSQL on port 5433, Memurai).

| Check | Result |
| --- | --- |
| `scripts\setup-local.ps1` | Completed: role and databases created, migrations 0001 and 0002 applied, 178 npm packages installed |
| Backend lint (ruff) | Passed |
| Migrations match models (`alembic check`) | Passed |
| Backend tests (pytest) | 70 passed. The first run had 21 failures, all caused by the Redis connection described below |
| Frontend type check, lint and production build | Passed in the first run |
| Defect found and fixed | On Windows `localhost` resolved to IPv6 first. A Redis connection by that name took 2.08 s against a 2.0 s timeout. The Redis URLs now use `127.0.0.1` |
| Defect found and fixed | The rule `models/` in `.gitignore` also matched `backend/app/db/models/`, so six source files were missing from the first commit. The rule is anchored and `tools\checks\ignored_source_check.py` guards against a repeat |

## Verified on the owner's machine, 12 October 2026

Recorded from the owner's own command output and screenshots (Windows 11, GPU notebook for the model).

| Check | Result |
| --- | --- |
| Model worker on a GPU notebook, connected with `scripts\connect-worker.ps1`, backend started with `scripts\dev.ps1` | Readiness reported the provider as `ok` with model `NCAIR1/N-ATLaS`. The worker kept reporting `ready` for more than an hour |
| `scripts\smoke-day1.ps1`, first run | Failed at "PostgreSQL is reachable" because the PostgreSQL Windows service was stopped. Redis and the provider were `ok` |
| `scripts\smoke-day1.ps1`, after the service was started | Every required step passed and the script printed `N-ATLAS inference: VERIFIED (the real model returned generated text)`. One request returned the reply `Hello!` with 44 tokens in 626 ms. The dashboard counted the request |
| `GET /api/v1/models` with a project API key from PowerShell | Returned the model record with status `available` |
| Dashboard in a browser | Showed N-ATLAS inference as Connected with the model identifier |
| Git: first-run fixes merged, `develop` and `competition-mvp` created | Done. The six model files are tracked |

## Verified on the deployed instance, 14 October 2026

Recorded from the owner's own build log, command output and screenshots.

| Check | Result |
| --- | --- |
| `scripts\check.ps1` before the merge | After the local Redis service was started, every gate passed, including 87 backend tests (70 earlier tests plus 17 for web app serving) |
| Image build on the deployment platform | Built from `main` on the first attempt: web app build, backend install, non-root user, health check. The container started and the platform reported the deployment live in 62 seconds |
| `scripts\smoke-day1.ps1` against the live address, over HTTPS | Every required step passed: readiness `ok` for PostgreSQL, Redis and the provider, registration, CSRF protection, project and API key, gateway authentication, one real chat request (reply `Hello!`, 44 tokens, 448 ms), the dashboard counted it, revoke, archive and logout. The script printed `N-ATLAS inference: VERIFIED` |
| Web app in a browser at the live address | Dashboard, project list and project pages render with their fonts and styles under the Content-Security-Policy. Sign-in works after the public address was set. The N-ATLAS inference card shows Connected |

## Added after the competition submission

| Item | State |
| --- | --- |
| `integrations/` (Evaluation Lab engine, voice pipeline and speech worker, MCP server) merged as a standalone library | Files are byte-identical to the delivered package (100 files). Its author reports 501 unit tests on Linux. Re-run here in a sandbox without the MCP SDK and Starlette: the evaluation, security, voice and MCP service test modules pass (12 of 19 modules). The other seven need those packages and were not run |
| Wiring of those modules into the gateway, database and screens | **Not done.** The library is not imported by the backend yet. Evaluation, speech and MCP are not available in the product |
| Production container (`Dockerfile`, `docker/entrypoint.sh`) and web app served by the API process (`backend/app/web/spa.py`) | Built and deployed, see the section above |

## Not yet executed

| Item | Why it is unverified |
| --- | --- |
| Browser console on the live site | The pages render correctly. Content-Security-Policy messages in the console were not inspected |
| `scripts\prod-env.ps1` | Statically checked only. The variables may have been entered by hand |
| Database backups and a restore test on the platform | Not set up |
| Worker unit tests | The worker environment is not created on the owner's machine |
| A persistent model host | The model runs on a temporary notebook. See [model-hosting.md](model-hosting.md) |
| Playground, SDKs, request log viewer, metrics charts | Not built |
| Speech recognition inference | No speech worker has been run |
| The interface at narrow widths and the landing page in a real browser | Not recorded |

## Rules for public copy

No invented metrics, partners, users or performance. Every figure comes from stored data or is labelled as an example. No em dashes. No security certification claims. Capabilities that are not built are labelled "In development" in `frontend/src/config.ts`.
