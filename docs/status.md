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
| Owner verification | Done on 18 October 2026: step 6b of the live smoke test (Playground, browser session, no API key) passed, see the section below |

## Added in the Evaluation Lab build, 18 October 2026

| Item | State |
| --- | --- |
| Backend module `backend/app/modules/evaluation/` (adapter, database store, routes, dashboard hook) | Written. The pure logic is tested here against the real library: 22 tests in `test_evaluation_mapping.py` and `test_evaluation_adapter.py` run on every starter dataset and pass, and a mutation check (stop stripping NUL characters, remove the rate-limit retry, drop the repeat count) made the right tests fail |
| Integration tests `backend/tests/test_evaluation.py` (about 16 tests: auth, ownership, create, run, cancel, export, dashboard, store read path, limits) | Written, compiled and checked by `static_check.py`. **Not run**: they need PostgreSQL and Redis. Run `scripts\check.ps1` |
| Evaluation pages (`frontend/src/extensions/evaluation/`) | Type-checked (strict, no unused locals). Bundled with the real app shell and driven in headless Chromium against a mock API built from fixtures that the real engine produced (a completed run, a run with model errors, a cancelled run): 25 checks including progress and saving, cancel, export links, retries, a 404, desktop and phone widths, clean console. Not yet seen against the real backend |
| Production image | `Dockerfile` now installs `integrations/` before the backend. The library builds from the minimal Docker context and loads its starter files when installed (checked here). The platform build on 18 October 2026 was the first real Docker run and it succeeded, see the section below |
| Phone layout | Three root causes found by measuring, fixed in `layout.css` and `components.css`: the page container's grid column followed the widest table, and absolutely positioned table header labels escaped the scrolling wrapper |
| Privacy and security copy | "Prompts and responses are not stored" was true only for request logs. The privacy page, security page and docs now say the Evaluation Lab stores dataset prompts and run outputs, and that deleting runs from the web app is not available yet |
| Known limits | A run in progress when the service restarts is lost. The list can miss a run for a moment between "completed" and "saved". Datasets are immutable and their version label is not stored. `request_log_id` and `triggered_by` are not filled in. Runs cannot be deleted from the web app |
| Owner verification | Done on 18 October 2026, see the section below |

## Verified on the deployed instance, 18 October 2026

Recorded from the owner's own command output and build log (Windows 11, deployment platform build 682fcf33 of commit c690b7eb, the merge of pull request 6).

| Check | Result |
| --- | --- |
| `scripts\check.ps1` before the merge | All seven gates passed: the evaluation library is installed, ruff, `alembic check` (no new upgrade operations), 134 backend tests (one deprecation warning from the test client), type check, lint and production build. The worker tests were skipped because no worker environment exists on that machine |
| `scripts\install-package.ps1` | Verified 55 files, refused the dry run because 26 files differed, then installed with `-Force` after backing those files up |
| Image build on the deployment platform | 25 steps. The library installed before the backend, the container started and the platform reported the deployment live in 49 seconds |
| `GET /api/v1/health/ready` on the live address | `ok` for the database, Redis and the provider, with model `NCAIR1/N-ATLaS` |
| `scripts\smoke-day1.ps1` against the live address | Every required step passed, including 6b (Playground) and 6c (Evaluation Lab). The starter dataset ran against the real model: pass rate 0.875 (7 passed, 1 failed, 0 needs review, 0 errors) and was exported as JSON. One chat request returned `Hello!` with 44 tokens in 502 ms. The dashboard counted 12 requests. The script printed `N-ATLAS inference: VERIFIED` |
| MCP compatibility probe (`scripts\probe-mcp-compat.ps1`) | Verdict COMPATIBLE: the SDK (mcp 2.3.0) installed next to the pinned versions, `pip check` was clean, and 235 library tests ran (4 skipped) and passed. **Limits of that verdict:** it ran on Python 3.13.9 while the production image runs 3.12, and its pins were FastAPI 0.143.2, Starlette 1.8.0 and SQLAlchemy 2.0.54 while the live build installed FastAPI 0.146.0, Starlette 1.9.0 and SQLAlchemy 2.0.55 |

## Added in the product positioning build, 19 October 2026

| Item | State |
| --- | --- |
| Landing page rewritten as a product page | The hero says what Forge does for a developer. Below it: a live status strip, the capability tables (available and in development), a four-step start, the two web app tools, a "what to expect in early access" section with the real limits, and a closing call to action. It makes no claim about pricing, uptime, users or performance. Three corrections were made on the way: the "Evaluate" step said that layer was in development although it is available; the hero button "Explore the playground" scrolled to the request builder, which does not call a model; and the page expanded the N-ATLAS acronym, which `integrations/docs/n-atlas-verified-facts.md` says Forge copy does not do |
| Live status | Reads `GET /api/v1/health/ready` once per page load. By default the server caches its probe of the model host for 10 seconds, so visitors do not add load to the host. An absent model is shown as a warning that names the `provider_unavailable` error, a 503 as a platform fault, and a failed request as "Unknown" |
| Dashboard | A "Get started" checklist computed from data the dashboard already loads (it disappears once all four steps are done) and a "Platform" panel that lists every capability with its status and a link. No backend change |
| Capability registry | `layers` in `frontend/src/config.ts` is the single source for the landing tables and the dashboard panel. It gained `appPath` and `docsPath` and the entry "Request logs and charts" (in development), and "Evaluation" is now "Evaluation Lab". `challengeUrl` is removed |
| Challenge wording | Removed from the landing page, the footer, `README.md`, `SECURITY.md` (which also named `develop`; fixes now go on `main`) and `docs/security.md`. Left on purpose: the factual note on how official N-ATLAS API credentials are issued (`docs/n-atlas-integration.md` and the Connect section of the in-app docs), the `competition-mvp` branch history, and the `integrations/` library docs |
| Documentation | `docs/evaluation-lab.md` (linked from the README, this page and `config.py` but missing from the repository), `tools/README.md` and `tools/make_package.py` come from a later build of the Evaluation package that never reached the repository. The in-app documentation has a new "Playground and Evaluations" section |
| MCP probe pins | `scripts\probe-mcp-compat.ps1` pinned three versions that the live build no longer installs. They now match the build log of deployment 682fcf33 (checked by script). The verdict above predates the fix, so run the probe again |
| Verification here | Strict type check against stand-in declarations: clean. Copy lint: clean. Browser harness against a mock API, in headless Chromium: landing page 15 checks, dashboard 12, documentation 4, and the existing Playground (18) and Evaluation Lab (25) suites still pass. Four of five deliberate breaks (a request outside the 30 day count, an absent model shown as success, MCP marked available, the challenge link back in the footer) were caught by the right check. The fifth, removing a grid safeguard on the capability tables, is not caught at any width tested, because the stacked phone layout prevents that overflow by other means. The browser run also found a page that grew to 530 px at 390 px, which is fixed. Not run: backend tests, because this build changes no backend code |
| Known limits | The sandbox has no web fonts, so line breaks differ a little from production. Below 760 px the capability tables become stacked rows and rely on ARIA roles to keep their table semantics. Nothing here has been seen on the live address yet |

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
| MCP compatibility on the live versions | The probe ran on Python 3.13.9 against older pins. The pins are fixed. Run it again, ideally on Python 3.12 like the image |
| Pinned backend dependencies | The production image installs the newest allowed versions, so one deployment can differ from the last (FastAPI 0.143.2 in an earlier build log, 0.146.0 on 18 October). Pin them before adding the MCP SDK |
| The landing page and the dashboard on the live address | Driven in headless Chromium in the build sandbox only (see the positioning build above). Not yet seen on the live address |

## Rules for public copy

No invented metrics, partners, users or performance. Every figure comes from stored data or is labelled as an example. No em dashes. No security certification claims. Capabilities that are not built are labelled "In development" in `frontend/src/config.ts`.
