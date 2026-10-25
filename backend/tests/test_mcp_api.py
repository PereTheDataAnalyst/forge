"""MCP client management and the /mcp endpoint. Integration tests: they need PostgreSQL and Redis.

The model worker is a scripted provider. The routes, session authentication, CSRF, ownership, the
API key service, audit logging and the key lookup used by the MCP endpoint are the real ones.
The last test needs the MCP SDK (the `mcp` extra of forge-integrations) and is skipped without it.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import bearer, db_fetch, signed_in_account
from tests.provider_double import install_scripted_provider

LIBRARY_SCOPES = {"models:read", "project:read", "usage:read", "generate", "transcribe", "evaluate"}
PROTOCOL = "2026-07-28"


@pytest.fixture(scope="module")
def mcp_app(migrated_database):
    app = create_app(make_settings(public_base_url="http://localhost:5173"))
    # The MCP endpoint only accepts the Host names the deployment owns.
    with TestClient(app, base_url="http://localhost:5173") as test_client:
        yield test_client, app, install_scripted_provider(app)


def new_project(account) -> str:
    response = account.request("POST", "/api/v1/projects", json={"name": "MCP project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def clients_path(project_id) -> str:
    return f"/api/v1/projects/{project_id}/mcp/clients"


def create_client(account, project_id, *, name="Claude Code", access=None, days=30):
    payload = {"name": name, "access": access or ["models:read", "project:read"]}
    payload["expires_in_days"] = days
    return account.request("POST", clients_path(project_id), json=payload)


def test_the_catalog_needs_a_session_and_lists_the_six_kinds_of_access(mcp_app):
    client, _, _ = mcp_app
    client.cookies.clear()
    assert client.get("/api/v1/mcp/catalog").status_code == 401
    account = signed_in_account(client)
    body = account.request("GET", "/api/v1/mcp/catalog").json()
    assert body["endpoint_path"] == "/mcp"
    assert {group["scope"] for group in body["groups"]} == LIBRARY_SCOPES
    assert set(body["default_access"]) <= {"models:read", "project:read"}
    assert body["expiry_days"] == [30, 90] and body["default_expiry_days"] == 30
    assert body["state"] in {"ready", "unavailable"}
    assert body["available"] == (body["state"] == "ready")
    assert body["message"]
    tools = {tool for group in body["groups"] for tool in group["tools"]}
    assert tools == {
        "get_model_info", "get_project_info", "get_usage",
        "generate", "chat", "translate", "transcribe", "evaluate",
    }  # fmt: skip


def test_a_client_is_a_scoped_key_that_is_shown_once(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    response = create_client(account, project_id, access=["generate", "models:read", "generate"])
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["key"].startswith("nfk_") and created["key_prefix"] == created["key"][:14]
    assert created["name"] == "Claude Code" and created["status"] == "active"
    assert created["access"] == ["models:read", "generate"]
    assert created["allowed_tools"] == ["get_model_info", "generate", "chat", "translate"]
    assert created["expires_at"] and created["last_seen_at"] is None and created["revoked_at"] is None

    listed = account.request("GET", clients_path(project_id)).json()["data"]
    assert [item["id"] for item in listed] == [created["id"]]
    assert "key" not in listed[0] and created["key"] not in json.dumps(listed)

    keys = account.request("GET", f"/api/v1/projects/{project_id}/api-keys").json()["data"]
    (key,) = keys
    assert key["scopes"] == ["mcp.invoke"] and key["name"] == "MCP: Claude Code"
    assert key["prefix"] == created["key_prefix"] and key["expires_at"] == created["expires_at"]


def test_an_mcp_key_cannot_use_the_rest_gateway(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    created = create_client(account, new_project(account), access=["generate", "models:read"]).json()
    for method, path, body in (
        ("POST", "/api/v1/chat", {"messages": [{"role": "user", "content": "Hello"}]}),
        ("GET", "/api/v1/models", None),
        ("POST", "/api/v1/audio/transcriptions?language=yo", None),
    ):
        client.cookies.clear()
        response = client.request(method, path, headers=bearer(created["key"]), json=body)
        client.cookies.clear()
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "insufficient_scope"


def test_bad_client_requests_are_refused(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    path = clients_path(project_id)
    good = {"name": "Cursor", "access": ["models:read"], "expires_in_days": 30}
    cases = [
        ({**good, "name": "   "}, 422, "validation_error"),
        ({**good, "name": "x" * 61}, 422, "validation_error"),
        ({**good, "name": "bad\u0007name"}, 422, "validation_error"),
        ({**good, "access": []}, 422, "validation_error"),
        ({**good, "access": ["admin"]}, 422, "invalid_access"),
        ({**good, "access": ["models:read", "root"]}, 422, "invalid_access"),
        ({**good, "expires_in_days": 7}, 422, "validation_error"),
        ({**good, "expires_in_days": 365}, 422, "validation_error"),
        ({**good, "unexpected": 1}, 422, "validation_error"),
    ]
    for payload, status, code in cases:
        response = account.request("POST", path, json=payload)
        assert response.status_code == status, (payload, response.text)
        assert response.json()["error"]["code"] == code, payload
    assert account.request("GET", path).json()["data"] == []  # nothing half-created
    assert account.request("GET", f"/api/v1/projects/{project_id}/api-keys").json()["data"] == []

    no_csrf = account.request("POST", path, json=good, with_csrf=False)
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_validation_failed"
    client.cookies.clear()
    assert client.post(path, json=good).status_code == 401
    stranger = signed_in_account(client)
    assert stranger.request("POST", path, json=good).status_code == 404
    assert stranger.request("GET", path).status_code == 404


def test_a_project_has_a_limit_on_active_clients_and_revoking_frees_a_place(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    made = [create_client(account, project_id, name=f"Client {n}").json() for n in range(10)]
    refused = create_client(account, project_id, name="One too many")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "mcp_client_limit_reached"
    revoke = account.request("POST", f"{clients_path(project_id)}/{made[0]['id']}/revoke")
    assert revoke.status_code == 200
    assert create_client(account, project_id, name="Fits now").status_code == 201


def test_revoking_a_client_revokes_its_key_and_is_safe_to_repeat(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    created = create_client(account, project_id).json()
    path = f"{clients_path(project_id)}/{created['id']}/revoke"
    first = account.request("POST", path)
    assert first.status_code == 200 and first.json()["status"] == "revoked"
    assert first.json()["revoked_at"]
    again = account.request("POST", path)
    assert again.status_code == 200 and again.json()["status"] == "revoked"
    (key,) = account.request("GET", f"/api/v1/projects/{project_id}/api-keys").json()["data"]
    assert key["status"] == "revoked"
    assert account.request("GET", clients_path(project_id)).json()["data"][0]["status"] == "revoked"
    missing = account.request("POST", f"{clients_path(project_id)}/{'0' * 8}-0000-4000-8000-{'0' * 12}/revoke")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "mcp_client_not_found"
    stranger = signed_in_account(client)
    assert stranger.request("POST", path).status_code == 404


def test_revoking_the_key_from_the_keys_page_also_stops_the_client(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    created = create_client(account, project_id).json()
    (key,) = account.request("GET", f"/api/v1/projects/{project_id}/api-keys").json()["data"]
    assert account.request("POST", f"/api/v1/projects/{project_id}/api-keys/{key['id']}/revoke").status_code == 200
    assert account.request("GET", clients_path(project_id)).json()["data"][0]["status"] == "revoked"
    assert created["status"] == "active"


def test_creating_and_revoking_are_audited_without_the_key(mcp_app):
    client, _, _ = mcp_app
    account = signed_in_account(client)
    project_id = new_project(account)
    created = create_client(account, project_id, name="Audited").json()
    account.request("POST", f"{clients_path(project_id)}/{created['id']}/revoke")
    import uuid

    rows = db_fetch(
        "select action, details::text as details from audit_logs where project_id = $1 order by created_at",
        uuid.UUID(project_id),
    )
    actions = [row["action"] for row in rows]
    assert "mcp_client.create" in actions and "mcp_client.revoke" in actions
    assert all(created["key"] not in row["details"] for row in rows)


def test_the_endpoint_rejects_a_request_without_a_token(mcp_app):
    client, app, _ = mcp_app
    client.cookies.clear()
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    if app.state.mcp.state == "ready":
        assert response.status_code == 401
        assert "bearer" in response.headers.get("www-authenticate", "").lower()
    else:
        assert response.status_code == 503
        assert response.json()["error"]["code"] in {"mcp_unavailable", "mcp_disabled"}


def rpc(client, key, method, params=None, *, name=None):
    headers = {
        **bearer(key),
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = name
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "forge-tests", "version": "1"},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**(params or {}), "_meta": meta}}
    client.cookies.clear()
    response = client.post("/mcp", headers=headers, json=body)
    client.cookies.clear()
    return response


def test_a_real_mcp_client_lists_tools_calls_them_and_is_cut_off_on_revoke(mcp_app):
    pytest.importorskip("mcp", reason="needs the MCP SDK (forge-integrations[mcp])")
    client, app, provider = mcp_app
    if app.state.mcp.state != "ready":
        pytest.fail(f"the MCP server did not start: {app.state.mcp.message}")
    account = signed_in_account(client)
    project_id = new_project(account)
    created = create_client(
        account, project_id, access=["models:read", "project:read", "generate", "usage:read"]
    ).json()
    key = created["key"]

    listed = rpc(client, key, "tools/list")
    assert listed.status_code == 200, listed.text
    assert listed.headers["x-request-id"]
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert names == {"get_model_info", "get_project_info", "get_usage", "generate", "chat", "translate"}
    assert not names & {"transcribe", "evaluate"}  # not granted to this client

    info = rpc(client, key, "tools/call", {"name": "get_project_info", "arguments": {}}, name="get_project_info")
    assert info.status_code == 200 and "MCP project" in info.text

    said = rpc(
        client, key, "tools/call",
        {"name": "generate", "arguments": {"prompt": "Say hello", "language": "en"}}, name="generate",
    )  # fmt: skip
    assert said.status_code == 200 and "Scripted reply" in said.text
    import uuid

    rows = db_fetch(
        "select source, outcome, endpoint, api_key_id from requests where project_id = $1 and source = 'mcp'",
        uuid.UUID(project_id),
    )
    assert len(rows) == 1 and rows[0]["outcome"] == "success" and rows[0]["endpoint"] == "/mcp"
    assert "Say hello" not in str(db_fetch("select details::text from audit_logs where project_id = $1", uuid.UUID(project_id)))

    ungranted = rpc(client, key, "tools/call", {"name": "transcribe", "arguments": {}}, name="transcribe")
    assert ungranted.status_code in (200, 400, 403) and "Scripted reply" not in ungranted.text

    (last_seen,) = account.request("GET", clients_path(project_id)).json()["data"]
    assert last_seen["last_seen_at"] is not None

    account.request("POST", f"{clients_path(project_id)}/{created['id']}/revoke")
    assert rpc(client, key, "tools/list").status_code == 401
    assert provider.calls  # the model was reached through the platform gateway
