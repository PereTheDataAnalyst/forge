# Tools

| Folder or file | What it is | Who runs it |
| --- | --- | --- |
| `checks/` | Dependency-free checkers (static names, schema parity, PowerShell lint, copy lint, ignored-source check), the frontend stub type check, and `sandbox_run.py` for backend logic tests without the web framework. See `checks/README.md` | The engineer's sandbox, and the owner for `copy_lint.py` and `ignored_source_check.py` |
| `ui-harness/` | Real-browser tests of the real pages against a mock API. See `ui-harness/README.md` | The engineer's sandbox |
| `make_package.py` | Builds a package from the diff against the owner's repository state and proves it with a simulated install. See its docstring | The engineer's sandbox |

None of this is part of the production image (`.dockerignore` excludes `tools`).
