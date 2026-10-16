"""Playground endpoints. Integration tests: they need PostgreSQL and Redis (see conftest.py).

The N-ATLAS worker is replaced by a scripted provider that exists only in these tests. The
routes, session authentication, CSRF and Origin checks, project ownership, the gateway, request
logging and rate limiting are the real ones.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.base import ProviderUnavailable
from tests.conftest import make_settings
from tests.helpers import Account, bearer, create_project_and_key, db_fetch, signed_in_account
from tests.provider_double import MODEL_ID, install_scripted_provider

PROMPT = "Ẹ kú àárọ̀, ṣe daadaa ni?"
CHAT = {"messages": [{"role": "user", "content": PROMPT}], "language": "yo"}


@pytest.fixture(scope="module")
def scripted(migrated_database):
    app = create_app(make_settings())
    with TestClient(app) as test_client:
        yield test_client, install_scripted_provider(app)


def new_project(account: Account) -> str:
    response = account.request("POST", "/api/v1/projects", json={"name": "Playground project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def chat_path(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/playground/chat"


def models_path(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/playground/models"


def test_playground_requires_a_browser_session(scripted):
    client, _ = scripted
    project_id = str(uuid.uuid4())
    for method, path in (("GET", models_path(project_id)), ("POST", chat_path(project_id))):
        response = client.request(method, path, json=CHAT if method == "POST" else None)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "not_authenticated"


def test_an_api_key_does_not_open_the_playground(scripted):
    client, _ = scripted
    account = signed_in_account(client)
    project, created = create_project_and_key(account)
    client.cookies.clear()
    response = client.post(chat_path(project["id"]), headers=bearer(created["key"]), json=CHAT)
    client.cookies.clear()
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_csrf_and_origin_are_enforced_for_chat(scripted):
    client, provider = scripted
    account = signed_in_account(client)
    project_id = new_project(account)
    calls_before = len(provider.calls)

    no_token = account.request("POST", chat_path(project_id), json=CHAT, with_csrf=False)
    assert no_token.status_code == 403
    assert no_token.json()["error"]["code"] == "csrf_validation_failed"

    foreign_origin = account.request(
        "POST", chat_path(project_id), json=CHAT, headers={"Origin": "https://evil.example"}
    )
    assert foreign_origin.status_code == 403
    assert foreign_origin.json()["error"]["code"] == "origin_not_allowed"
    assert len(provider.calls) == calls_before


def test_other_users_projects_look_like_missing_ones(scripted):
    client, provider = scripted
    owner = signed_in_account(client)
    project_id = new_project(owner)
    intruder = signed_in_account(client)
    calls_before = len(provider.calls)

    chat = intruder.request("POST", chat_path(project_id), json=CHAT)
    models = intruder.request("GET", models_path(project_id))
    for response in (chat, models):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
    assert len(provider.calls) == calls_before


def test_chat_returns_the_reply_and_is_logged_as_a_playground_request(scripted):
    client, provider = scripted
    account = signed_in_account(client)
    project_id = new_project(account)

    response = account.request(
        "POST",
        chat_path(project_id),
        json={
            "messages": [
                {"role": "system", "content": "Answer in Yoruba."},
                {"role": "user", "content": PROMPT},
            ],
            "language": "yo",
            "temperature": 0.3,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == {"role": "assistant", "content": "Scripted reply"}
    assert body["model"] == MODEL_ID and body["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["x-ratelimit-limit"] == "1000"

    sent = provider.calls[-1]
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert sent.params.temperature == 0.3
    assert sent.params.max_tokens == 512

    rows = db_fetch(
        "SELECT source, user_id, api_key_id, endpoint, outcome, status_code, language, model "
        "FROM requests WHERE project_id = $1",
        uuid.UUID(project_id),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "playground"
    assert row["user_id"] == uuid.UUID(account.user_id)
    assert row["api_key_id"] is None
    assert row["endpoint"] == "/api/v1/projects/{project_id}/playground/chat"
    assert (row["outcome"], row["status_code"], row["language"]) == ("success", 200, "yo")
    assert row["model"] == MODEL_ID
    # Prompts and replies are never stored with the request log.
    assert all(PROMPT not in str(value) and "Scripted reply" not in str(value) for value in row.values())


def test_provider_failure_is_a_structured_error_and_is_logged(scripted):
    client, provider = scripted
    account = signed_in_account(client)
    project_id = new_project(account)
    provider.replies.append(ProviderUnavailable())

    response = account.request("POST", chat_path(project_id), json=CHAT)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "provider_unavailable"
    assert error["request_id"] == response.headers["x-request-id"]

    rows = db_fetch(
        "SELECT source, outcome, error_code, status_code FROM requests WHERE project_id = $1",
        uuid.UUID(project_id),
    )
    assert [(r["source"], r["outcome"], r["error_code"], r["status_code"]) for r in rows] == [
        ("playground", "error", "provider_unavailable", 503)
    ]


def test_chat_validation_matches_the_gateway(scripted):
    client, provider = scripted
    account = signed_in_account(client)
    project_id = new_project(account)
    calls_before = len(provider.calls)

    def post(body):
        return account.request("POST", chat_path(project_id), json=body)

    assert post({"messages": []}).json()["error"]["code"] == "validation_error"
    assert post({**CHAT, "unexpected": 1}).json()["error"]["code"] == "validation_error"
    assert post({**CHAT, "temperature": 5}).status_code == 422

    not_user_last = post(
        {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}
    )
    assert not_user_last.status_code == 422
    assert not_user_last.json()["error"]["code"] == "invalid_messages"

    too_many_tokens = post({**CHAT, "max_tokens": 5000})
    assert too_many_tokens.status_code == 422
    assert too_many_tokens.json()["error"]["code"] == "invalid_parameters"

    unknown_model = post({**CHAT, "model": "gpt-4o"})
    assert unknown_model.status_code == 404
    assert unknown_model.json()["error"]["code"] == "model_not_found"
    assert len(provider.calls) == calls_before


def test_models_lists_only_text_models_with_their_status(scripted):
    client, _ = scripted
    account = signed_in_account(client)
    project_id = new_project(account)

    response = account.request("GET", models_path(project_id))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [model["id"] for model in data] == [MODEL_ID]
    assert data[0]["kind"] == "llm" and data[0]["status"] == "available"
    assert data[0]["languages"] == ["en", "ha", "ig", "yo"]


def test_playground_rate_limit_is_metered_per_user(migrated_database):
    app = create_app(make_settings(gateway_rate_limit_per_minute=2))
    with TestClient(app) as limited:
        install_scripted_provider(app)
        account = Account(limited)
        assert account.register().status_code == 201
        project_id = new_project(account)
        statuses = [
            account.request("POST", chat_path(project_id), json=CHAT).status_code
            for _ in range(2)
        ]
        blocked = account.request("POST", chat_path(project_id), json=CHAT)
    assert statuses == [200, 200]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
