"""Run backend test modules that need no database, in a sandbox without the web framework.

Usage (from the repository root): python3 tools/checks/sandbox_run.py tests.test_evaluation_mapping tests.test_evaluation_adapter

Only IMPORT NAMES are stubbed (FastAPI, Starlette, SQLAlchemy). The code under test is the real
code: real AppError, real gateway contracts, real evaluation library, real module files. The
evaluation package's __init__ (which mounts routes) is skipped by pre-registering the package.
"""
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
stub("sqlalchemy")
stub("sqlalchemy.ext")
stub("sqlalchemy.ext.asyncio", AsyncSession=klass("AsyncSession"))

package = types.ModuleType("app.modules.evaluation")
package.__path__ = [str(ROOT / "backend" / "app" / "modules" / "evaluation")]
sys.modules["app.modules.evaluation"] = package

passed = failed = 0
for module_name in sys.argv[1:]:
    module = importlib.import_module(module_name)
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_") or function.__module__ != module_name:
            continue
        started = time.perf_counter()
        try:
            function()
        except Exception:
            failed += 1
            print(f"FAIL {module_name}.{name}")
            print("    " + traceback.format_exc().strip().replace("\n", "\n    "))
        else:
            passed += 1
            print(f"PASS {name}  ({(time.perf_counter() - started) * 1000:.0f} ms)")
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
