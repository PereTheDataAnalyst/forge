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

## Not yet executed

Run `scripts\check.ps1` and `scripts\smoke-day1.ps1` and treat any failure as the first task.

| Item | Why it is unverified |
| --- | --- |
| Backend start-up, migrations, `alembic check`, pytest integration tests | No PostgreSQL, Redis or package installs in the build sandbox |
| `npm install`, ESLint, `vite build` with the real packages | No network in the build sandbox |
| Every PowerShell script | No PowerShell interpreter in the build sandbox |
| `scripts\dev.ps1` and `scripts\smoke-day1.ps1` after the Redis fix | Not run again yet |
| The interface in a real browser | Never viewed |
| Worker tests | The worker environment is not created on the owner's machine |
| The inference worker with the real model | The owner's machine has no GPU. The notebook in [gpu-host-notebook.md](gpu-host-notebook.md) was tested only against stand-ins, and its first real run is pending |
| Real N-ATLAS output through the gateway | Depends on the item above |

## Rules for public copy

No invented metrics, partners, users or performance. Every figure comes from stored data or is labelled as an example. No em dashes. No security certification claims. Capabilities that are not built are labelled "In development" in `frontend/src/config.ts`.
