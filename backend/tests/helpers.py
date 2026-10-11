"""Helpers for integration tests."""

import asyncio
import os
import uuid
from typing import Any

import asyncpg
import httpx
from fastapi.testclient import TestClient

PASSWORD = "Correct-Horse-Battery-9"
SAFE_ORIGIN = "http://localhost:5173"
CHAT = {"messages": [{"role": "user", "content": "Hello"}]}


def unique_email() -> str:
    return f"dev-{uuid.uuid4().hex[:12]}@example.com"


def db_fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    """Run a read-only query directly against the test database."""
    dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

    async def run() -> list[asyncpg.Record]:
        connection = await asyncpg.connect(dsn)
        try:
            return await connection.fetch(sql, *args)
        finally:
            await connection.close()

    return asyncio.run(run())


class Account:
    """A browser session with its own cookies, independent of other accounts."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.cookies: dict[str, str] = {}
        self.csrf: str | None = None
        self.email = unique_email()
        self.user_id: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        with_csrf: bool = True,
        with_origin: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        merged = dict(headers or {})
        if self.cookies:
            merged["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            if with_csrf and self.csrf:
                merged["X-CSRF-Token"] = self.csrf
            if with_origin:
                merged.setdefault("Origin", SAFE_ORIGIN)
        self.client.cookies.clear()
        response = self.client.request(method, path, json=json, headers=merged, **kwargs)
        self.client.cookies.clear()
        for name, value in response.cookies.items():
            self.cookies[name] = value
        if "forge_csrf" in response.cookies:
            self.csrf = response.cookies["forge_csrf"]
        return response

    def register(self, password: str = PASSWORD) -> httpx.Response:
        response = self.request(
            "POST",
            "/api/v1/auth/register",
            json={"email": self.email, "password": password, "display_name": "Test Developer"},
        )
        if response.status_code == 201:
            self.user_id = response.json()["id"]
        return response


def signed_in_account(client: TestClient) -> Account:
    account = Account(client)
    response = account.register()
    assert response.status_code == 201, response.text
    return account


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def gateway(
    client: TestClient, method: str, path: str, key: str, json: Any = None
) -> httpx.Response:
    """Call the gateway with an API key and no browser cookies."""
    client.cookies.clear()
    response = client.request(method, path, headers=bearer(key), json=json)
    client.cookies.clear()
    return response


def create_project_and_key(
    account: Account, scopes: list[str] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = account.request("POST", "/api/v1/projects", json={"name": "Test project"}).json()
    payload: dict[str, Any] = {"name": "Server key"}
    if scopes is not None:
        payload["scopes"] = scopes
    response = account.request(
        "POST", f"/api/v1/projects/{project['id']}/api-keys", json=payload
    )
    assert response.status_code == 201, response.text
    return project, response.json()
