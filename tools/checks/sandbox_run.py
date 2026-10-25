"""Run backend test modules that need no database, in a sandbox without the web framework.

Usage (from the repository root): python3 tools/checks/sandbox_run.py tests.test_evaluation_mapping tests.test_evaluation_adapter

Only IMPORT NAMES are stubbed (FastAPI, Starlette, SQLAlchemy). The code under test is the real
code: real AppError, real gateway contracts, real evaluation library, real module files. The
__init__ of every extension package (which mounts routes) is skipped by pre-registering the
package. A test written as `async def` is run to completion, so it can never pass by never running.
"""
import asyncio
import importlib
import inspect
import sys
import time
import traceback
import types
from pathlib import Path

import os
ROOT = Path(os.environ.get("FORGE_ROOT", Path(__file__).resolve().parents[2]))
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "integrations" / "src")]


def stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def klass(name):
    return type(name, (), {})


stub("fastapi", FastAPI=klass("FastAPI"), Request=klass("Request"))
stub("fastapi.exceptions", RequestValidationError=klass("RequestValidationError"))
stub("fastapi.responses", JSONResponse=klass("JSONResponse"))
stub("starlette")
stub("starlette.exceptions", HTTPException=klass("HTTPException"))


class Route:  # records how it was built; the real one is Starlette's
    def __init__(self, path, endpoint, **options):
        self.path, self.endpoint, self.options = path, endpoint, options


stub("starlette.routing", Route=Route)
stub("sqlalchemy")
stub("sqlalchemy.ext")
stub("sqlalchemy.ext.asyncio", AsyncSession=klass("AsyncSession"))

modules_dir = ROOT / "backend" / "app" / "modules"
for directory in sorted(modules_dir.iterdir()):
    if directory.is_dir() and (directory / "__init__.py").exists():
        package = types.ModuleType(f"app.modules.{directory.name}")
        package.__path__ = [str(directory)]
        sys.modules[f"app.modules.{directory.name}"] = package

passed = failed = 0
for module_name in sys.argv[1:]:
    module = importlib.import_module(module_name)
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_") or function.__module__ != module_name:
            continue
        started = time.perf_counter()
        try:
            outcome = function()
            if inspect.iscoroutine(outcome):
                asyncio.run(outcome)
        except Exception:
            failed += 1
            print(f"FAIL {module_name}.{name}")
            print("    " + traceback.format_exc().strip().replace("\n", "\n    "))
        else:
            passed += 1
            print(f"PASS {name}  ({(time.perf_counter() - started) * 1000:.0f} ms)")
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
