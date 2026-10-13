"""Serving the web app from the API process. No services required."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.web.spa import HTML_CSP, mount_spa, resolve_file, resolve_request

INDEX_MARKER = "id='root'"


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "brand").mkdir()
    (root / "index.html").write_text(f"<!doctype html><div {INDEX_MARKER}></div>")
    (root / "assets" / "app-abc123.js").write_text("console.log('forge');")
    (root / "assets" / "font.woff2").write_bytes(b"wOF2")
    (root / "brand" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    return root.resolve()


def build_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_exception_handlers(app)

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/only-post")
    async def only_post() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture()
def web(dist: Path) -> TestClient:
    app = build_app()
    assert mount_spa(app, str(dist)) is True
    return TestClient(app)


def test_root_serves_the_index_with_a_strict_policy(web: TestClient):
    response = web.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == HTML_CSP
    assert response.headers["cache-control"] == "no-cache"
    assert INDEX_MARKER in response.text


@pytest.mark.parametrize("path", ["/login", "/docs", "/app/projects/0b0c1d2e"])
def test_client_side_routes_fall_back_to_the_index(web: TestClient, path: str):
    response = web.get(path)
    assert response.status_code == 200
    assert INDEX_MARKER in response.text


def test_hashed_assets_are_cached_for_a_year(web: TestClient):
    response = web.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert "javascript" in response.headers["content-type"]


def test_web_fonts_have_a_font_type(web: TestClient):
    assert web.get("/assets/font.woff2").headers["content-type"] == "font/woff2"


def test_public_files_are_served_and_revalidated(web: TestClient):
    response = web.get("/brand/logo.png")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_a_missing_asset_is_a_json_404(web: TestClient):
    response = web.get("/assets/missing.js")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "<html" not in response.text.lower()


def test_unknown_api_paths_stay_a_json_404(web: TestClient):
    response = web.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_api_routes_are_untouched(web: TestClient):
    assert web.get("/api/v1/ping").json() == {"ok": True}
    wrong_method = web.get("/api/v1/only-post")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"


def test_a_post_to_an_unknown_path_is_a_json_404(web: TestClient):
    response = web.post("/anything")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_head_request_for_a_client_route_has_no_body(web: TestClient):
    response = web.head("/app/projects")
    assert response.status_code == 200
    assert response.content == b""


def test_dotfiles_are_never_served(web: TestClient, dist: Path):
    (dist / ".env").write_text("SECRET_KEY=leaked")
    response = web.get("/.env")
    assert "leaked" not in response.text


def test_traversal_never_reaches_files_outside_the_build(web: TestClient):
    for path in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "TOP SECRET" not in web.get(path).text


def test_resolve_file_rules(dist: Path):
    assert resolve_file(dist, "assets/app-abc123.js") == dist / "assets" / "app-abc123.js"
    for blocked in (
        "../secret.txt",
        "assets/../../secret.txt",
        "..\\secret.txt",
        "/etc/passwd",
        "assets/app\x00.js",
        ".env",
        "assets",
        "assets/missing.js",
        "",
    ):
        assert resolve_file(dist, blocked) is None, blocked


def test_resolve_request_rules(dist: Path):
    index = dist / "index.html"
    assert resolve_request(dist, "/") == ("index", index)
    assert resolve_request(dist, "/app/projects/1") == ("index", index)
    assert resolve_request(dist, "/assets/app-abc123.js")[0] == "file"
    assert resolve_request(dist, "/assets/missing.js") == ("none", None)
    assert resolve_request(dist, "/api") == ("none", None)
    assert resolve_request(dist, "/api/v1/anything") == ("none", None)
    assert resolve_request(dist, "/apiary")[0] == "index"


def test_mount_is_a_noop_without_a_build(tmp_path: Path):
    app = build_app()
    assert mount_spa(app, None) is False
    assert mount_spa(app, "  ") is False
    assert mount_spa(app, str(tmp_path / "nothing")) is False
    response = TestClient(app).get("/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
