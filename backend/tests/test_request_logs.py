"""Request log viewer and metrics. Integration tests: they need PostgreSQL and Redis (see conftest.py).

The model worker is a scripted provider. Everything else is real: routes, session authentication,
project ownership, the gateway that writes the request rows, and the SQL that reads them back.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.base import ProviderUnavailable
from tests.conftest import make_settings
from tests.helpers import CHAT, bearer, create_project_and_key, gateway, signed_in_account
from tests.provider_double import MODEL_ID, install_scripted_provider

YORUBA = {"messages": [{"role": "user", "content": "Ẹ kú àárọ̀"}], "language": "yo"}


@pytest.fixture(scope="module")
def scripted(migrated_database):
    app = create_app(make_settings())
    with TestClient(app) as test_client:
        yield test_client, install_scripted_provider(app)


def seed(client, provider):
    """A project with 3 good API requests, 1 failed API request and 1 Playground request."""
    account = signed_in_account(client)
    project, key = create_project_and_key(account)
    for _ in range(3):
        sent = gateway(client, "POST", "/api/v1/chat", key["key"], json=YORUBA)
        assert sent.status_code == 200, sent.text
    provider.replies.append(ProviderUnavailable())
    failed = gateway(client, "POST", "/api/v1/chat", key["key"], json=CHAT)
    assert failed.status_code == 503, failed.text
    played = account.request(
        "POST", f"/api/v1/projects/{project['id']}/playground/chat", json=YORUBA
    )
    assert played.status_code == 200, played.text
    return account, project, key


def base(project) -> str:
    return f"/api/v1/projects/{project['id']}/requests"


def test_the_log_endpoints_need_a_session_and_belong_to_the_owner_alone(scripted):
    client, provider = scripted
    account, project, key = seed(client, provider)
    for suffix in ("", "/metrics", "/facets"):
        client.cookies.clear()
        assert client.get(base(project) + suffix).status_code == 401
        client.cookies.clear()
        assert client.get(base(project) + suffix, headers=bearer(key["key"])).status_code == 401
    stranger = signed_in_account(client)
    for suffix in ("", "/metrics", "/facets"):
        response = stranger.request("GET", base(project) + suffix)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
    assert account.request("GET", base(project)).status_code == 200


def test_the_list_is_newest_first_and_each_row_says_who_and_what(scripted):
    client, provider = scripted
    account, project, key = seed(client, provider)
    body = account.request("GET", base(project)).json()
    rows = body["data"]
    assert len(rows) == 5 and body["next_cursor"] is None
    stamps = [row["created_at"] for row in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert sorted(row["source"] for row in rows) == ["api_key"] * 4 + ["playground"]
    api_rows = [row for row in rows if row["source"] == "api_key"]
    assert {row["api_key_prefix"] for row in api_rows} == {key["prefix"]}
    assert {row["api_key_id"] for row in api_rows} == {key["id"]}
    playground = next(row for row in rows if row["source"] == "playground")
    assert playground["api_key_id"] is None and playground["api_key_prefix"] is None
    good = [row for row in api_rows if row["outcome"] == "success"]
    assert len(good) == 3
    assert all(row["status_code"] == 200 and row["error_code"] is None for row in good)
    assert all(row["prompt_tokens"] == 7 and row["completion_tokens"] == 3 for row in good)
    assert all(row["endpoint"] == "/api/v1/chat" and row["method"] == "POST" for row in good)
    # A log row never carries what was said.
    assert "Ẹ kú" not in str(rows) and "Scripted reply" not in str(rows)


def test_filters_narrow_the_list(scripted):
    client, provider = scripted
    account, project, key = seed(client, provider)

    def fetch(query):
        response = account.request("GET", f"{base(project)}?{query}")
        assert response.status_code == 200, response.text
        return response.json()["data"]

    failed = fetch("outcome=error")
    assert len(failed) == 1
    assert (failed[0]["error_code"], failed[0]["status_code"]) == ("provider_unavailable", 503)
    assert len(fetch("outcome=success")) == 4
    assert len(fetch("source=playground")) == 1 and len(fetch("source=api_key")) == 4
    assert len(fetch("source=mcp")) == 0 and len(fetch("source=voice")) == 0
    assert len(fetch("language=yo")) == 4
    assert len(fetch("error_code=provider_unavailable")) == 1
    assert len(fetch("status_code=503")) == 1 and len(fetch("status_code=200")) == 4
    assert len(fetch(f"api_key_id={key['id']}")) == 4
    assert len(fetch(f"model={MODEL_ID}")) >= 4
    assert len(fetch("endpoint=/api/v1/chat")) == 4
    assert len(fetch("min_latency_ms=3600000")) == 0
    target = fetch("outcome=error")[0]["request_id"]
    assert [row["request_id"] for row in fetch(f"request_id={target[:10]}")] == [target]
    assert fetch("request_id=req_zzzzzzzz") == []
    assert len(fetch("outcome=success&source=api_key&language=yo")) == 3


def test_pages_follow_the_cursor_without_repeats_or_gaps(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    seen, sizes, cursor = [], [], None
    for _ in range(10):
        query = "limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = account.request("GET", f"{base(project)}?{query}").json()
        sizes.append(len(body["data"]))
        seen.extend(row["id"] for row in body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sizes == [2, 2, 1] and len(seen) == len(set(seen)) == 5
    everything = [row["id"] for row in account.request("GET", base(project)).json()["data"]]
    assert seen == everything


def test_bad_input_is_a_clear_422(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    cases = {
        "cursor=not-a-cursor": "invalid_cursor",
        "request_id=has space": "invalid_request_id",
        "range=2d": "validation_error",
        "limit=0": "validation_error",
        "limit=101": "validation_error",
        "source=bogus": "validation_error",
        "outcome=ok": "validation_error",
        "error_code=Bad-Code": "validation_error",
        "status_code=99": "validation_error",
        "api_key_id=nope": "validation_error",
    }
    for query, code in cases.items():
        response = account.request("GET", f"{base(project)}?{query}")
        assert response.status_code == 422, (query, response.text)
        assert response.json()["error"]["code"] == code, query


def test_metrics_add_up_to_what_was_sent(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    body = account.request("GET", base(project) + "/metrics?range=24h").json()
    totals = body["totals"]
    assert (totals["requests"], totals["successful"], totals["failed"]) == (5, 4, 1)
    assert totals["success_rate"] == 0.8
    assert totals["prompt_tokens"] == 28 and totals["completion_tokens"] == 12
    assert totals["total_tokens"] == 40
    latency = totals["latency"]
    assert latency["avg_ms"] is not None and latency["p50_ms"] is not None
    assert latency["p95_ms"] is not None and latency["p95_ms"] >= latency["p50_ms"]

    assert body["range"] == "24h" and body["bucket_seconds"] == 3600
    series = body["series"]
    assert 24 <= len(series) <= 25
    starts = [point["start"] for point in series]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert sum(point["requests"] for point in series) == 5
    assert sum(point["failed"] for point in series) == 1
    assert sum(point["prompt_tokens"] for point in series) == 28
    assert all(point["failed"] == point["requests"] - point["successful"] for point in series)
    quiet = [point for point in series if point["requests"] == 0]
    assert quiet and all(point["avg_latency_ms"] is None for point in quiet)

    breakdowns = body["breakdowns"]
    by_source = {item["key"]: item for item in breakdowns["by_source"]}
    assert (by_source["api_key"]["requests"], by_source["api_key"]["failed"]) == (4, 1)
    assert by_source["playground"]["requests"] == 1
    assert breakdowns["by_error_code"] == [
        {"key": "provider_unavailable", "requests": 1, "failed": 1}
    ]
    statuses = {item["key"]: item["requests"] for item in breakdowns["by_status"]}
    assert statuses == {"200": 4, "503": 1}
    assert {item["key"]: item["requests"] for item in breakdowns["by_language"]}["yo"] == 4


def test_metrics_follow_the_same_filters_and_range_as_the_list(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    failed = account.request("GET", base(project) + "/metrics?outcome=error").json()
    assert failed["totals"]["requests"] == 1 and failed["totals"]["successful"] == 0
    assert failed["totals"]["latency"]["avg_ms"] is None  # only successes are timed
    assert sum(point["requests"] for point in failed["series"]) == 1
    hour = account.request("GET", base(project) + "/metrics?range=1h").json()
    assert hour["bucket_seconds"] == 300 and 12 <= len(hour["series"]) <= 13
    assert hour["totals"]["requests"] == 5
    for name, step in (("6h", 900), ("7d", 21600), ("30d", 86400), ("90d", 259200)):
        other = account.request("GET", base(project) + f"/metrics?range={name}").json()
        assert other["bucket_seconds"] == step and other["totals"]["requests"] == 5


def test_a_project_with_no_traffic_has_honest_zeros(scripted):
    client, _ = scripted
    account = signed_in_account(client)
    project = account.request("POST", "/api/v1/projects", json={"name": "Quiet"}).json()
    body = account.request("GET", base(project) + "/metrics").json()
    totals = body["totals"]
    assert totals["requests"] == 0 and totals["success_rate"] is None
    assert totals["latency"] == {"avg_ms": None, "p50_ms": None, "p95_ms": None}
    assert all(point["requests"] == 0 for point in body["series"])
    assert all(value == [] for value in body["breakdowns"].values())
    assert account.request("GET", base(project)).json() == {"data": [], "next_cursor": None}


def test_facets_list_what_exists_ignoring_other_filters(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    facets = account.request("GET", base(project) + "/facets").json()
    assert MODEL_ID in facets["models"]
    assert facets["error_codes"] == ["provider_unavailable"]
    assert "/api/v1/chat" in facets["endpoints"]


def test_one_projects_traffic_never_appears_in_another(scripted):
    client, provider = scripted
    account, project, _ = seed(client, provider)
    other = account.request("POST", "/api/v1/projects", json={"name": "Other"}).json()
    assert account.request("GET", base(other)).json()["data"] == []
    assert account.request("GET", base(other) + "/metrics").json()["totals"]["requests"] == 0
