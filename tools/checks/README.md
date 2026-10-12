# Verification tools

Dependency-free checks that work when packages cannot be installed (for example in a restricted build sandbox). They complement, and never replace, `scripts\check.ps1`, which runs the real toolchain.

Run from the repository root:

```
python tools/checks/static_check.py backend                      # undefined names and first-party import mismatches
python tools/checks/static_check.py inference/natlas-worker
python tools/checks/schema_check.py backend                      # ORM models versus Alembic migrations
python tools/checks/ps_check.py scripts                          # PowerShell: ASCII, balanced delimiters, no PowerShell 7 only syntax
python tools/checks/copy_lint.py                                 # em dashes, banned phrases, emoji, obsolete company name
python tools/checks/ignored_source_check.py                      # source-looking files that .gitignore hides (needs Git)
python tools/checks/mini_runner.py backend tests.test_primitives # fixture-free tests without pytest
```

Frontend type check without `node_modules`, using stand-in declarations for React, React Router and Vite (TypeScript must be available):

```
node <path-to>/typescript/bin/tsc -p tools/checks/frontend/tsconfig.json
```

`static_check.py` and `schema_check.py` contain no hard-coded paths. They were validated with seeded-bug canaries: each must fail on deliberately broken input.

`ignored_source_check.py` exists because the rule `models/` once hid `backend/app/db/models/` from Git. It fails on that rule and passes on the anchored rules now in `.gitignore`.
