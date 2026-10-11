"""Discovers optional extension modules under app/modules/.

An extension module is a package `app.modules.<name>` that may expose:
  router    an APIRouter, mounted under /api/v1
  register  a callable `register(app)` for hooks, scopes, body-limit overrides, providers.
            It may append coroutine functions to `app.state.startup_hooks` and
            `app.state.shutdown_hooks`; each receives the FastAPI app.
A missing module is skipped. A module that exists but fails to import raises, so errors are
never hidden.
"""

import importlib

from fastapi import FastAPI

from app.core.logging import get_logger

log = get_logger(__name__)

EXTENSION_MODULES: tuple[str, ...] = ("evaluation", "voice", "mcp")


def load_extension_modules(app: FastAPI) -> list[str]:
    loaded: list[str] = []
    for name in EXTENSION_MODULES:
        path = f"app.modules.{name}"
        try:
            module = importlib.import_module(path)
        except ModuleNotFoundError as exc:
            if exc.name == path:
                continue
            raise
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api/v1")
        register = getattr(module, "register", None)
        if callable(register):
            register(app)
        loaded.append(name)
        log.info("extension.loaded", extra={"extension": name})
    return loaded
