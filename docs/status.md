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
| Wiring of those modules into the gateway, database and screens | **Evaluation is wired** (see the Evaluation Lab build below). **Speech and MCP are not**: nothing imports them, so they are not available in the product |
| Production container (`Dockerfile`, `docker/entrypoint.sh`) and web app served by the API process (`backend/app/web/spa.py`) | Built and deployed, see the section above |

## Added in the playground build, 15 October 2026

| Item | State |
| --- | --- |
| Playground routes (`backend/app/api/v1/playground.py`) and tests (`backend/tests/test_playground.py`, `provider_double.py`) | Written, compiled and checked by `static_check.py`. **Not run**: the tests need PostgreSQL and Redis, which were not available where this was built. Run `scripts\check.ps1` |
| Playground page (`frontend/src/extensions/playground/`) | Type-checked with the stub project (strict, no unused locals). Bundled with the real app shell and driven in headless Chromium against a mock API: 18 checks covering send, CSRF header, history, regenerate, parameter validation, provider error with request ID, stop, generated code in four languages, laptop, wide and phone widths, and a clean console. Not yet seen against the real backend or the real model |
| Navigation bar at 390 px | Found by that browser run: with four items the bar widened the whole page. Fixed in `layout.css` and re-checked |
| Snippet builder (`lib/snippets.ts`) | Output for every existing caller is byte-identical to `main` (8 outputs compared). "Language not set" now omits the field, where it used to send an empty string the API rejects |
| Owner verification | Pending: `check.ps1`, then `smoke-day1.ps1` against the live address with the model connected |

## Added in the Evaluation Lab build, 17 October 2026

| Item | State |
| --- | --- |
| Backend module `backend/app/modules/evaluation/` (adapter, database store, routes, dashboard hook) | Written. The pure logic is tested here against the real library: 22 tests in `test_evaluation_mapping.py` and `test_evaluation_adapter.py` run on every starter dataset and pass, and a mutation check (stop stripping NUL characters, remove the rate-limit retry, drop the repeat count) made the right tests fail |
| Integration tests `backend/tests/test_evaluation.py` (about 16 tests: auth, ownership, create, run, cancel, export, dashboard, store read path, limits) | Written, compiled and checked by `static_check.py`. **Not run**: they need PostgreSQL and Redis. Run `scripts\check.ps1` |
| Evaluation pages (`frontend/src/extensions/evaluation/`) | Type-checked (strict, no unused locals). Bundled with the real app shell and driven in headless Chromium against a mock API built from fixtures that the real engine produced (a completed run, a run with model errors, a cancelled run): 25 checks including progress and saving, cancel, export links, retries, a 404, desktop and phone widths, clean console. Not yet seen against the real backend |
| Production image | `Dockerfile` now installs `integrations/` before the backend. The library builds from the minimal Docker context and loads its starter files when installed (checked here). The platform build is the first real Docker run |
| Phone layout | Three root causes found by measuring, fixed in `layout.css` and `components.css`: the page container's grid column followed the widest table, and absolutely positioned table header labels escaped the scrolling wrapper |
| Privacy and security copy | "Prompts and responses are not stored" was true only for request logs. The privacy page, security page and docs now say the Evaluation Lab stores dataset prompts and run outputs, and that deleting runs from the web app is not available yet |
| Known limits | A run in progress when the service restarts is lost. The list can miss a run for a moment between "completed" and "saved". Datasets are immutable and their version label is not stored. `request_log_id` and `triggered_by` are not filled in. Runs cannot be deleted from the web app |
| Owner verification | Pending: install the library into the backend environment, `check.ps1`, then the smoke test against the live address with the model connected |

## Not yet executed

| Item | Why it is unverified |
| --- | --- |
| Browser console on the live site | The pages render correctly. Content-Security-Policy messages in the console were not inspected |
| `scripts\prod-env.ps1` | Statically checked only. The variables may have been entered by hand |
| Database backups and a restore test on the platform | Not set up |
| Worker unit tests | The worker environment is not created on the owner's machine |
| A persistent model host | The model runs on a temporary notebook. See [model-hosting.md](model-hosting.md) |
| SDKs, request log viewer, metrics charts | Not built |
| Speech recognition inference | No speech worker has been run |
| The interface at narrow widths and the landing page in a real browser | Not recorded |

## Rules for public copy

No invented metrics, partners, users or performance. Every figure comes from stored data or is labelled as an example. No em dashes. No security certification claims. Capabilities that are not built are labelled "In development" in `frontend/src/config.ts`.
