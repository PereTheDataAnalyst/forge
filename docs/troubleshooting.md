# Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `running scripts is disabled on this system` | PowerShell execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force`, which affects only the current window |
| `psql.exe was not found` | PostgreSQL `bin` folder is not on `PATH` | Add it, for example `C:\Program Files\PostgreSQL\16\bin`, or rerun setup with `-SkipDatabase` if the databases exist |
| `Could not connect to PostgreSQL as 'postgres'` | Wrong administrator password or service stopped | Check `Get-Service postgresql*` and retype the password |
| Backend log says `startup.redis_unreachable` | Memurai is not running | `Start-Service Memurai` in an administrator window |
| `/api/v1/health/ready` returns 503 | PostgreSQL or Redis is down | The response names which one |
| `/api/v1/health/ready` says `degraded` | No inference worker is connected | Expected until you run `connect-worker.ps1`. See [n-atlas-integration.md](n-atlas-integration.md) |
| Chat returns `provider_not_configured` | `NATLAS_INFERENCE_URL` is unset | `.\scripts\connect-worker.ps1` then restart the backend |
| Chat returns `provider_loading` | The model is still loading | Wait, then retry. Loading an 8B model takes minutes |
| Chat returns `provider_misconfigured` | Gateway and worker tokens differ | Run `connect-worker.ps1` again so both use the same token |
| Worker log shows a 401 or 403 from Hugging Face | Terms not accepted or no token | Accept the terms on the model page, set `HF_TOKEN` |
| Worker reports `busy` or out of memory | Not enough GPU memory for the weights plus the cache | Lower `max_tokens`, or use a larger GPU |
| Browser requests fail with `origin_not_allowed` | The page is served from an origin not in the allowlist | Use `http://localhost:5173`, or set `PUBLIC_BASE_URL` or `CORS_ALLOWED_ORIGINS` |
| `csrf_validation_failed` | The CSRF cookie is missing | Reload the page, which restores it through `/auth/me` |
| Backend refuses to start with a message about `SECRET_KEY` | `.env` still holds the placeholder | Run `.\scripts\setup-local.ps1` or set a real random secret |
| Tests are all skipped | `TEST_DATABASE_URL` is not set | Run `.\scripts\check.ps1`, which loads it from `.env` |
| Tests refuse to run | The database name lacks `test`, or Redis is on database 0 to 9 | Use `forge_test` and `redis://127.0.0.1:6379/15` |
| `npm ci` fails | `frontend/package-lock.json` is not committed | Commit the lock file created by `npm install` |
| `alembic check` reports differences | A model changed without a migration | Add a revision after the latest one |
| Port 5173 or 8000 is in use | Another process holds it | Stop it. The frontend uses `strictPort` so it will not silently move |
| Readiness or sign-in returns 503 `rate_limiter_unavailable` although Memurai is running | On Windows, `localhost` can resolve to IPv6 first and the first attempt costs about 2 seconds, which equals the Redis timeout | Use `127.0.0.1` in `REDIS_URL` and `TEST_REDIS_URL`. Check with a client the application uses, not only a port test |
| A new source file or folder is missing on GitHub | A `.gitignore` rule also matches it. The rule `models/` once hid `backend/app/db/models/` | `git check-ignore -v <path>`, anchor the rule with a leading slash, then run `python tools\checks\ignored_source_check.py` |
| GPU notebook: cell 2 says the session cannot reach the internet | Internet is off for the Kaggle session | Right-hand panel, Session options, Internet On, then run the cell again |
| Kaggle accelerator options or the Internet switch are faint | The account has no verified phone number | Verify it under Phone Verification at kaggle.com/settings, then reload the notebook |
| GPU notebook: cell 3 says Hugging Face refused access | Terms not accepted, request still pending, or the token belongs to another account | Accept the terms on the model page and create the token in that same account |
| GPU notebook: cell 6 reports that the worker exited or the model failed | Wrong accelerator, not enough GPU memory, or a library problem | Read the log tail the cell prints. The accelerator must be GPU T4 x2 |
| Backend reports the provider as unreachable after the notebook was restarted | The temporary tunnel address changed | `.\scripts\connect-worker.ps1 -Url <new address>`, then restart the backend |
| Readiness reports the database as `unavailable` and the smoke test fails at "PostgreSQL is reachable" | The PostgreSQL Windows service is stopped. A port test on the database port fails too | Start it from an administrator PowerShell, for example `Start-Service postgresql-x64-18`, or from Services. A normal window answers "Cannot open service" |
| The container exits at start in a deployment and the log names a setting | A required variable is missing, still a placeholder, or too short | See the table in [deployment.md](deployment.md). Validation messages never print the value |
| Register or sign-in fails in a deployment with `origin_not_allowed` | `PUBLIC_BASE_URL` differs from the address in the browser | Set it to the exact address, with scheme and no path, and redeploy |
