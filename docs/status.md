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

## Not yet executed anywhere

These need a machine with PostgreSQL, Redis, network access and PowerShell. Run `scripts\check.ps1` and `scripts\smoke-day1.ps1` and treat any failure as the first task.

| Item | Why it is unverified |
| --- | --- |
| Backend start-up, migrations, `alembic check`, pytest integration tests | No PostgreSQL, Redis or package installs in the build sandbox |
| `npm install`, ESLint, `vite build` with the real packages | No network in the build sandbox |
| Every PowerShell script | No PowerShell interpreter in the build sandbox |
| The inference worker with the real model | Needs Hugging Face access and enough memory |
| Real N-ATLAS output through the gateway | Depends on the item above |

## Rules for public copy

No invented metrics, partners, users or performance. Every figure comes from stored data or is labelled as an example. No em dashes. No security certification claims. Capabilities that are not built are labelled "In development" in `frontend/src/config.ts`.
