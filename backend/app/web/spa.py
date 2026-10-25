"""Serves the built web app from the same origin as the API.

The production image runs one process that answers /api/* and the web app. One origin keeps the
session and CSRF cookies first-party and needs no CORS configuration.

Design rules:

* Routing is untouched. The web app answers only when no route matched at all (a 404), so API
  paths keep their exact behaviour, including 405 for a wrong method on a real endpoint.
* Anything under /api that did not match stays a JSON 404 and is never answered with HTML.
* A missing file that looks like an asset (it has an extension) is a real 404, so a broken
  deployment is visible instead of returning HTML where a script was expected.
* Paths are resolved and must stay inside the build directory. Dotfiles are never served.

`resolve_request` is plain Python so the path rules can be tested without a web framework.
"""

import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse

from app.core.errors import handle_http_exception
from app.core.logging import get_logger

log = get_logger(__name__)

# Some platforms have no MIME entry for web fonts. With X-Content-Type-Options: nosniff a wrong
# type makes the browser reject the file.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("application/manifest+json", ".webmanifest")

# The build uses no inline scripts and loads nothing from other origins. React applies its
# style props through the CSSOM, which this policy permits. media-src allows blob: so the Voice
# page can play back a recording made in the browser before it is sent.
HTML_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"

Kind = Literal["index", "file", "none"]


def resolve_file(root: Path, relative: str) -> Path | None:
    """Return the regular file at `relative` inside `root`, or None.

    `root` must already be resolved. Traversal outside the root, NUL bytes and any path segment
    that starts with a dot all give None.
    """
    if not relative or "\x00" in relative:
        return None
    segments = relative.replace("\\", "/").split("/")
    if any(segment.startswith(".") for segment in segments):
        return None
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def resolve_request(root: Path, url_path: str) -> tuple[Kind, Path | None]:
    """Decide what answers a request that no API route matched.

    Returns ("file", path) for a real file, ("index", index.html) for a client-side route and
    ("none", None) when the normal JSON 404 should be used.
    """
    relative = url_path.lstrip("/")
    if relative == "api" or relative.startswith("api/"):
        return "none", None
    if relative:
        found = resolve_file(root, relative)
        if found is not None:
            return "file", found
        if Path(relative).suffix:
            return "none", None
    return "index", root / "index.html"


def _html_headers() -> dict[str, str]:
    return {"Cache-Control": REVALIDATE_CACHE, "Content-Security-Policy": HTML_CSP}


def _response_for(kind: Kind, path: Path | None, url_path: str) -> Response | None:
    if kind == "none" or path is None:
        return None
    if kind == "index" or path.suffix.lower() == ".html":
        return FileResponse(path, media_type="text/html; charset=utf-8", headers=_html_headers())
    immutable = url_path.lstrip("/").startswith("assets/")
    return FileResponse(
        path, headers={"Cache-Control": IMMUTABLE_CACHE if immutable else REVALIDATE_CACHE}
    )


def mount_spa(app: FastAPI, dist_dir: str | None) -> bool:
    """Serve the web app from `dist_dir` when it holds a build. Returns whether it is enabled."""
    directory = (dist_dir or "").strip()
    if not directory:
        return False
    root = Path(directory).resolve()
    if not (root / "index.html").is_file():
        log.warning("spa.build_not_found")
        return False

    async def handler(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404 and request.method in ("GET", "HEAD"):
            kind, path = resolve_request(root, request.url.path)
            served = _response_for(kind, path, request.url.path)
            if served is not None:
                return served
        return await handle_http_exception(request, exc)

    app.add_exception_handler(StarletteHTTPException, handler)  # type: ignore[arg-type]
    log.info("spa.enabled")
    return True
