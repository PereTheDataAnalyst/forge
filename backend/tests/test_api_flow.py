"""Integration tests for the HTTP API. Require PostgreSQL and Redis (see conftest.py)."""

import re
import uuid

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import (
    CHAT,
    PASSWORD,
    Account,
    bearer,
    create_project_and_key,
    db_fetch,
    gateway,
    signed_in_account,
    unique_email,
)

# --- platform basics ----------------------------------------------------------------------


def test_health_live_and_security_headers(client):
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_readiness_reports_core_services_and_unconfigured_provider(client):
    response = client.get("/api/v1/health/ready")
    body = response.json()
    assert response.status_code == 200, body
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
    assert body["checks"]["provider"]["status"] == "not_configured"


def test_request_id_is_echoed_or_generated(client):
    echoed = client.get("/api/v1/health/live", headers={"X-Request-ID": "my-test-request-0001"})
    assert echoed.headers["x-request-id"] == "my-test-request-0001"
    replaced = client.get("/api/v1/health/live", headers={"X-Request-ID": "short"})
    assert replaced.headers["x-request-id"].startswith("req_")


def test_unknown_route_returns_structured_error(client):
    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "trace-unknown-001"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == "trace-unknown-001"
    assert "Traceback" not in response.text


def test_oversized_request_bodies_are_rejected(migrated_database):
    with TestClient(create_app(make_settings(max_request_body_bytes=1024))) as small:
        response = small.post(
            "/api/v1/auth/login", json={"email": "a@example.com", "password": "x" * 5000}
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


# --- authentication -----------------------------------------------------------------------


def test_register_login_session_and_logout_flow(client):
    account = Account(client)
    registered = account.register()
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["email"] == account.email
    assert "password" not in body and "password_hash" not in body

    cookies = registered.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("forge_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("forge_csrf="))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie

    me = account.request("GET", "/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == account.email

    forged = account.request("POST", "/api/v1/auth/logout", with_csrf=False)
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "csrf_validation_failed"
    assert account.request("POST", "/api/v1/auth/logout").status_code == 204
    assert account.request("GET", "/api/v1/auth/me").status_code == 401

    again = Account(client)
    again.email = account.email
    wrong = again.request(
        "POST",
        "/api/v1/auth/login",
        json={"email": account.email, "password": "Wrong-Password-1234"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    ok = again.request(
        "POST", "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert ok.status_code == 200
    assert again.request("GET", "/api/v1/auth/me").status_code == 200


def test_registration_validation(client):
    weak = Account(client).request(
        "POST",
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": "password123"},
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "weak_password"
    assert weak.json()["error"]["details"][0]["field"] == "password"

    bad_email = Account(client).request(
        "POST", "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert bad_email.status_code == 422
    assert bad_email.json()["error"]["code"] == "validation_error"

    extra_field = Account(client).request(
        "POST",
        "/api/v1/auth/register",
        json={"email": unique_email(), "password": PASSWORD, "is_admin": True},
    )
    assert extra_field.status_code == 422


def test_duplicate_registration_is_rejected_case_insensitively(client):
    first = signed_in_account(client)
    duplicate = Account(client)
    duplicate.email = first.email.upper()
    response = duplicate.register()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_registered"


def test_login_lockout_after_repeated_failures(client):
    account = signed_in_account(client)
    attacker = Account(client)
    for _ in range(5):
        failed = attacker.request(
            "POST",
            "/api/v1/auth/login",
            json={"email": account.email, "password": "Wrong-Password-1234"},
        )
        assert failed.status_code == 401
    locked = attacker.request(
        "POST", "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_temporarily_locked"
    assert int(locked.headers["retry-after"]) > 0


def test_unknown_account_login_is_indistinguishable_from_wrong_password(client):
    response = Account(client).request(
        "POST",
        "/api/v1/auth/login",
        json={"email": unique_email(), "password": "Wrong-Password-1234"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_csrf_and_origin_are_enforced_for_state_changes(client):
    account = signed_in_account(client)
    path, body = "/api/v1/projects", {"name": "Guarded project"}

    missing = account.request("POST", path, json=body, with_csrf=False)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_validation_failed"

    forged = account.request(
        "POST", path, json=body, headers={"X-CSRF-Token": "forged-token-value"}, with_csrf=False
    )
    assert forged.status_code == 403

    hostile = account.request("POST", path, json=body, headers={"Origin": "https://evil.example.org"})
    assert hostile.status_code == 403
    assert hostile.json()["error"]["code"] == "origin_not_allowed"

    login_from_hostile_origin = Account(client).request(
        "POST",
        "/api/v1/auth/login",
        json={"email": account.email, "password": PASSWORD},
        headers={"Origin": "https://evil.example.org"},
    )
    assert login_from_hostile_origin.status_code == 403

    assert account.request("POST", path, json=body).status_code == 201


# --- projects -----------------------------------------------------------------------------


def test_project_lifecycle_and_isolation(client):
    owner, other = signed_in_account(client), signed_in_account(client)
    created = owner.request(
        "POST",
        "/api/v1/projects",
        json={"name": "  Hausa FAQ bot  ", "description": "Customer support assistant"},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Hausa FAQ bot"
    assert project["active_key_count"] == 0

    assert [p["id"] for p in owner.request("GET", "/api/v1/projects").json()["data"]] == [
        project["id"]
    ]
    assert other.request("GET", "/api/v1/projects").json()["data"] == []

    for method, body in (("GET", None), ("PATCH", {"name": "Hijack"}), ("DELETE", None)):
        denied = other.request(method, f"/api/v1/projects/{project['id']}", json=body)
        assert denied.status_code == 404, (method, denied.text)

    updated = owner.request("PATCH", f"/api/v1/projects/{project['id']}", json={"name": "Renamed"})
    assert updated.status_code == 200 and updated.json()["name"] == "Renamed"
    assert owner.request("DELETE", f"/api/v1/projects/{project['id']}").status_code == 204
    assert owner.request("GET", f"/api/v1/projects/{project['id']}").status_code == 404


def test_project_validation(client):
    account = signed_in_account(client)
    assert account.request("POST", "/api/v1/projects", json={"name": "   "}).status_code == 422
    assert account.request("POST", "/api/v1/projects", json={"name": "x" * 81}).status_code == 422
    assert account.request("GET", "/api/v1/projects/not-a-uuid").status_code == 422


# --- API keys -----------------------------------------------------------------------------


def test_api_key_is_shown_once_and_stored_only_as_a_hash(client):
    account = signed_in_account(client)
    project, created = create_project_and_key(account)
    full_key = created["key"]
    assert re.fullmatch(r"nfk_[0-9a-f]{10}_[A-Za-z0-9_-]{43}", full_key)
    assert created["prefix"] == full_key[:14]
    assert created["status"] == "active"
    assert created["scopes"] == ["models.read", "chat.create"]

    listing = account.request("GET", f"/api/v1/projects/{project['id']}/api-keys")
    assert listing.status_code == 200
    assert full_key not in listing.text
    assert all("key" not in item for item in listing.json()["data"])

    rows = db_fetch(
        "SELECT key_hash, prefix, hash_algorithm FROM api_keys WHERE id = $1",
        uuid.UUID(created["id"]),
    )
    assert len(rows) == 1
    stored = rows[0]
    assert len(stored["key_hash"]) == 64 and stored["key_hash"] != full_key
    assert full_key not in " ".join(str(value) for value in stored.values())
    assert stored["prefix"] == created["prefix"]
    assert stored["hash_algorithm"] == "hmac-sha256-v1"


def test_key_management_is_limited_to_the_project_owner(client):
    owner, other = signed_in_account(client), signed_in_account(client)
    project, created = create_project_and_key(owner)
    base = f"/api/v1/projects/{project['id']}/api-keys"
    assert other.request("GET", base).status_code == 404
    assert other.request("POST", base, json={"name": "x"}).status_code == 404
    assert other.request("POST", f"{base}/{created['id']}/revoke").status_code == 404
    assert other.request("POST", f"{base}/{created['id']}/rotate", json={}).status_code == 404


def test_key_revoke_and_rotate_lifecycle(client):
    account = signed_in_account(client)
    project, first = create_project_and_key(account)
    base = f"/api/v1/projects/{project['id']}/api-keys"
    assert gateway(client, "GET", "/api/v1/models", first["key"]).status_code == 200

    rotated = account.request("POST", f"{base}/{first['id']}/rotate", json={})
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    new_key = body["new_key"]["key"]
    assert new_key != first["key"] and body["previous"]["status"] == "revoked"
    old = gateway(client, "GET", "/api/v1/models", first["key"])
    assert old.status_code == 401 and old.json()["error"]["code"] == "api_key_revoked"
    assert gateway(client, "GET", "/api/v1/models", new_key).status_code == 200

    new_id = body["new_key"]["id"]
    revoked = account.request("POST", f"{base}/{new_id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    after = gateway(client, "GET", "/api/v1/models", new_key)
    assert after.status_code == 401 and after.json()["error"]["code"] == "api_key_revoked"
    assert account.request("POST", f"{base}/{new_id}/revoke").status_code == 200

    stale = account.request("POST", f"{base}/{first['id']}/rotate", json={})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "api_key_not_active"


def test_rotation_with_grace_period_keeps_old_key_valid_temporarily(client):
    account = signed_in_account(client)
    project, first = create_project_and_key(account)
    base = f"/api/v1/projects/{project['id']}/api-keys"
    rotated = account.request(
        "POST", f"{base}/{first['id']}/rotate", json={"grace_period_seconds": 600}
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert body["previous"]["status"] == "active"
    assert body["previous"]["expires_at"] is not None
    assert gateway(client, "GET", "/api/v1/models", first["key"]).status_code == 200
    assert gateway(client, "GET", "/api/v1/models", body["new_key"]["key"]).status_code == 200


def test_scopes_are_validated_and_enforced(client):
    account = signed_in_account(client)
    project, created = create_project_and_key(account, scopes=["models.read"])
    assert created["scopes"] == ["models.read"]
    assert gateway(client, "GET", "/api/v1/models", created["key"]).status_code == 200
    denied = gateway(client, "POST", "/api/v1/chat", created["key"], json=CHAT)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "insufficient_scope"

    unknown = account.request(
        "POST",
        f"/api/v1/projects/{project['id']}/api-keys",
        json={"name": "bad", "scopes": ["admin.everything"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "invalid_scope"


# --- gateway ------------------------------------------------------------------------------


def test_gateway_authentication_failures(client):
    missing = client.get("/api/v1/models")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"
    assert missing.headers["www-authenticate"].startswith("Bearer")

    wrong_scheme = client.get("/api/v1/models", headers={"Authorization": "Basic abc"})
    assert wrong_scheme.json()["error"]["code"] == "invalid_api_key"
    malformed = client.get("/api/v1/models", headers=bearer("not-a-key"))
    assert malformed.status_code == 401
    unknown = client.get("/api/v1/models", headers=bearer("nfk_0000000000_" + "A" * 43))
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "invalid_api_key"

    # A browser session must never authorise the developer gateway.
    account = signed_in_account(client)
    via_cookie = account.request("GET", "/api/v1/models")
    assert via_cookie.status_code == 401
    assert via_cookie.json()["error"]["code"] == "missing_api_key"


def test_models_endpoint_reports_official_model_facts(client):
    account = signed_in_account(client)
    _, created = create_project_and_key(account)
    response = gateway(client, "GET", "/api/v1/models", created["key"])
    assert response.status_code == 200
    model = response.json()["data"][0]
    assert model["id"] == "NCAIR1/N-ATLaS" and model["kind"] == "llm"
    assert model["languages"] == ["en", "ha", "ig", "yo"]
    assert model["status"] == "not_configured"
    assert "Awarri" in model["attribution"]
    assert response.headers["x-ratelimit-limit"] == "1000"


def test_chat_without_connected_provider_fails_honestly_and_is_logged(client):
    account = signed_in_account(client)
    project, created = create_project_and_key(account)
    response = gateway(client, "POST", "/api/v1/chat", created["key"], json={**CHAT, "language": "yo"})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "provider_not_configured"
    assert error["request_id"] == response.headers["x-request-id"]

    project_id = uuid.UUID(project["id"])
    rows = db_fetch(
        "SELECT outcome, error_code, status_code, language, source FROM requests "
        "WHERE project_id = $1",
        project_id,
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row["outcome"], row["error_code"], row["status_code"]) == (
        "error",
        "provider_not_configured",
        503,
    )
    assert (row["language"], row["source"]) == ("yo", "api_key")

    usage = db_fetch(
        "SELECT request_count, error_count, success_count FROM usage WHERE project_id = $1",
        project_id,
    )
    assert len(usage) == 1
    assert (usage[0]["request_count"], usage[0]["error_count"], usage[0]["success_count"]) == (1, 1, 0)


def test_chat_input_validation(client):
    account = signed_in_account(client)
    project, created = create_project_and_key(account)
    key = created["key"]

    def post(body):
        return gateway(client, "POST", "/api/v1/chat", key, json=body)

    assert post({"messages": []}).status_code == 422
    assert post({"messages": [{"role": "robot", "content": "x"}]}).json()["error"]["code"] == (
        "validation_error"
    )
    assert post({**CHAT, "unexpected": 1}).json()["error"]["code"] == "validation_error"
    assert post({**CHAT, "temperature": 5}).status_code == 422

    cases = {
        "invalid_messages": [
            {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]},
            {
                "messages": [
                    {"role": "user", "content": "a"},
                    {"role": "system", "content": "b"},
                    {"role": "user", "content": "c"},
                ]
            },
            {"messages": [{"role": "user", "content": "   "}]},
        ],
        "invalid_parameters": [{**CHAT, "max_tokens": 5000}],
    }
    for expected_code, bodies in cases.items():
        for body in bodies:
            response = post(body)
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == expected_code

    unknown_model = post({**CHAT, "model": "gpt-4o"})
    assert unknown_model.status_code == 404
    assert unknown_model.json()["error"]["code"] == "model_not_found"

    # Unsupported model names must never create usage rows (unbounded cardinality).
    models = db_fetch(
        "SELECT DISTINCT model FROM usage WHERE project_id = $1", uuid.UUID(project["id"])
    )
    assert all(row["model"] == "NCAIR1/N-ATLaS" for row in models)


def test_gateway_rate_limit_returns_429_with_retry_after(client, migrated_database):
    account = signed_in_account(client)
    _, created = create_project_and_key(account)
    with TestClient(create_app(make_settings(gateway_rate_limit_per_minute=3))) as limited:
        statuses = [
            limited.get("/api/v1/models", headers=bearer(created["key"])).status_code
            for _ in range(3)
        ]
        blocked = limited.get("/api/v1/models", headers=bearer(created["key"]))
    assert statuses == [200, 200, 200]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


# --- dashboard and audit ------------------------------------------------------------------


def test_dashboard_requires_authentication(client):
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_dashboard_summary_uses_real_data_only(client):
    account = signed_in_account(client)
    empty = account.request("GET", "/api/v1/dashboard/summary").json()
    assert empty["totals"] == {
        "projects": 0,
        "active_api_keys": 0,
        "requests": 0,
        "successful": 0,
        "failed": 0,
        "average_latency_ms": None,
    }
    assert empty["recent_requests"] == []
    assert empty["recent_api_keys"] == []
    assert empty["recent_evaluation_runs"] == []
    assert empty["provider"]["status"] == "not_configured"

    _, created = create_project_and_key(account)
    gateway(client, "POST", "/api/v1/chat", created["key"], json=CHAT)
    summary = account.request("GET", "/api/v1/dashboard/summary").json()
    totals = summary["totals"]
    assert (totals["projects"], totals["active_api_keys"]) == (1, 1)
    assert (totals["requests"], totals["failed"], totals["successful"]) == (1, 1, 0)
    assert totals["average_latency_ms"] is None
    assert summary["recent_requests"][0]["error_code"] == "provider_not_configured"
    assert summary["recent_api_keys"][0]["prefix"] == created["prefix"]

    other = signed_in_account(client)
    assert other.request("GET", "/api/v1/dashboard/summary").json()["totals"]["requests"] == 0


def test_security_relevant_actions_are_audited_without_secrets(client):
    account = signed_in_account(client)
    project, created = create_project_and_key(account)
    account.request("POST", f"/api/v1/projects/{project['id']}/api-keys/{created['id']}/revoke")
    rows = db_fetch(
        "SELECT action FROM audit_logs WHERE actor_user_id = $1", uuid.UUID(account.user_id)
    )
    actions = {row["action"] for row in rows}
    assert {"auth.register", "auth.login", "project.create", "api_key.create", "api_key.revoke"} <= (
        actions
    )
    details = db_fetch(
        "SELECT details FROM audit_logs WHERE target_id = $1 AND action = 'api_key.create'",
        created["id"],
    )
    assert details and created["key"] not in str(details)
