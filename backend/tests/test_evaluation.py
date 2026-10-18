"""Evaluation Lab endpoints. Integration tests: they need PostgreSQL and Redis (see conftest.py).

The N-ATLAS worker is replaced by a scripted provider that exists only in these tests. The
routes, session authentication, CSRF, ownership, the evaluation engine, the gateway, request
logging, the database store and the dashboard are the real ones.
"""

import functools
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.evaluation.store import DatabaseRunStore
from tests.conftest import make_settings
from tests.helpers import Account, db_fetch, signed_in_account
from tests.provider_double import MODEL_ID, install_scripted_provider

# Three cases whose outcome is known: the scripted provider always answers "Scripted reply".
DATASET = {
    "name": "Lab test dataset",
    "description": "Three cases with known outcomes.",
    "cases": [
        {
            "id": "greets",
            "name": "Greets",
            "category": "general",
            "prompt": "Say hello.",
            "language": "en",
            "checks": [{"type": "not_empty"}, {"type": "contains_all", "values": ["Scripted"]}],
        },
        {
            "id": "wrong",
            "name": "Wrong answer",
            "category": "factual",
            "prompt": "What is two plus two?",
            "language": "en",
            "checks": [{"type": "contains_all", "values": ["four"]}],
        },
        {
            "id": "yoruba",
            "name": "Yoruba prompt",
            "category": "general",
            "prompt": "Ẹ kú àárọ̀, ṣe daadaa ni?",
            "language": "yo",
            "checks": [{"type": "not_empty"}],
        },
    ],
}

SIX_CASES = {
    "name": "Six slow cases",
    "cases": [
        {
            "id": f"c{i}",
            "name": f"Case {i}",
            "category": "general",
            "prompt": f"Prompt number {i}",
            "checks": [{"type": "not_empty"}],
        }
        for i in range(6)
    ],
}


@pytest.fixture(scope="module")
def lab(migrated_database):
    app = create_app(make_settings())
    with TestClient(app) as test_client:
        yield test_client, install_scripted_provider(app)


def new_project(account: Account) -> str:
    response = account.request("POST", "/api/v1/projects", json={"name": "Evaluation project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def evaluations_path(project_id: str, evaluation_id: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/evaluations"
    return f"{base}/{evaluation_id}" if evaluation_id else base


def runs_path(project_id: str, run_id: str | None = None, suffix: str = "") -> str:
    base = f"/api/v1/projects/{project_id}/evaluation-runs"
    return f"{base}/{run_id}{suffix}" if run_id else base


def create_dataset(account: Account, project_id: str, dataset=None) -> dict:
    response = account.request(
        "POST", evaluations_path(project_id), json={"dataset": dataset or DATASET}
    )
    assert response.status_code == 201, response.text
    return response.json()


def start_run(account: Account, project_id: str, evaluation_id: str, **extra):
    return account.request(
        "POST",
        runs_path(project_id),
        json={"evaluation_id": evaluation_id, **extra},
    )


def wait_for_run(account: Account, project_id: str, run_id: str, timeout: float = 30.0) -> dict:
    """Wait until the run has ended and been saved. The library marks a run completed just
    before it writes it to the database, so `persisted` is what proves the row exists."""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        response = account.request("GET", runs_path(project_id, run_id))
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] != "running" and body["handle"]["persisted"] is not None:
            return body
        time.sleep(0.05)
    raise AssertionError(f"the run did not finish in time: {body}")


def test_every_endpoint_needs_a_browser_session(lab):
    client, _ = lab
    project_id, evaluation_id = str(uuid.uuid4()), str(uuid.uuid4())
    calls = [
        ("GET", "/api/v1/evaluations/starters", None),
        ("GET", "/api/v1/evaluations/checks", None),
        ("GET", evaluations_path(project_id), None),
        ("POST", evaluations_path(project_id), {"starter_id": "starter-general-checks"}),
        ("GET", evaluations_path(project_id, evaluation_id), None),
        ("DELETE", evaluations_path(project_id, evaluation_id), None),
        ("POST", runs_path(project_id), {"evaluation_id": evaluation_id}),
        ("GET", runs_path(project_id), None),
        ("GET", runs_path(project_id, "a" * 32), None),
        ("POST", runs_path(project_id, "a" * 32, "/cancel"), None),
        ("GET", runs_path(project_id, "a" * 32, "/export"), None),
    ]
    for method, path, body in calls:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["error"]["code"] == "not_authenticated"


def test_starter_datasets_are_listed_without_their_cases(lab):
    client, _ = lab
    account = signed_in_account(client)
    response = account.request("GET", "/api/v1/evaluations/starters")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert {item["id"] for item in data} >= {"starter-general-checks", "starter-nigerian-languages"}
    for item in data:
        assert {"id", "name", "version", "description", "case_count"} <= set(item)
        assert "cases" not in item and item["case_count"] > 0


def test_the_available_checks_are_listed_with_their_parameters(lab):
    client, _ = lab
    account = signed_in_account(client)
    response = account.request("GET", "/api/v1/evaluations/checks")
    assert response.status_code == 200, response.text
    checks = {item["type"]: item for item in response.json()["data"]}
    assert {"not_empty", "contains_all", "exact_match", "language_match", "consistency"} <= set(
        checks
    )
    assert all(item["description"] and "params" in item for item in checks.values())
    assert checks["contains_all"]["params"]["values"]["required"] is True


def test_a_dataset_is_created_read_listed_and_archived(lab):
    client, _ = lab
    account = signed_in_account(client)
    project_id = new_project(account)

    created = create_dataset(account, project_id)
    assert created["name"] == "Lab test dataset" and created["case_count"] == 3
    assert [case["key"] for case in created["cases"]] == ["greets", "wrong", "yoruba"]
    assert created["cases"][0]["checks"][0]["type"] == "not_empty"
    assert created["cases"][2]["language"] == "yo"

    listed = account.request("GET", evaluations_path(project_id)).json()["data"]
    assert [(item["id"], item["case_count"]) for item in listed] == [(created["id"], 3)]

    detail = account.request("GET", evaluations_path(project_id, created["id"]))
    assert detail.status_code == 200
    assert [case["prompt"] for case in detail.json()["cases"]][2] == "Ẹ kú àárọ̀, ṣe daadaa ni?"

    archived = account.request("DELETE", evaluations_path(project_id, created["id"]))
    assert archived.status_code == 204
    assert account.request("GET", evaluations_path(project_id)).json()["data"] == []
    gone = account.request("GET", evaluations_path(project_id, created["id"]))
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "evaluation_not_found"

    actions = {
        row["action"]
        for row in db_fetch(
            "SELECT action FROM audit_logs WHERE target_id = $1", created["id"]
        )
    }
    assert actions == {"evaluation.create", "evaluation.archive"}


def test_a_starter_dataset_can_be_added_and_an_unknown_one_cannot(lab):
    client, _ = lab
    account = signed_in_account(client)
    project_id = new_project(account)

    added = account.request(
        "POST", evaluations_path(project_id), json={"starter_id": "starter-general-checks"}
    )
    assert added.status_code == 201, added.text
    assert added.json()["case_count"] == 8

    unknown = account.request("POST", evaluations_path(project_id), json={"starter_id": "nope"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"


def test_a_bad_dataset_is_refused_with_a_message_for_each_problem(lab):
    client, _ = lab
    account = signed_in_account(client)
    project_id = new_project(account)

    broken = {"name": "Broken", "cases": [{"id": "c1", "category": "general", "checks": []}]}
    response = account.request("POST", evaluations_path(project_id), json={"dataset": broken})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_dataset" and error["details"]
    assert all({"field", "message"} <= set(issue) for issue in error["details"])

    for body in (
        {},
        {"starter_id": "starter-general-checks", "dataset": DATASET},
        {"dataset": DATASET, "extra": 1},
    ):
        refused = account.request("POST", evaluations_path(project_id), json=body)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "validation_error"
    assert account.request("GET", evaluations_path(project_id)).json()["data"] == []


def test_datasets_and_runs_belong_to_their_project_and_user(lab):
    client, _ = lab
    owner = signed_in_account(client)
    project_id = new_project(owner)
    other_project_id = new_project(owner)
    evaluation = create_dataset(owner, project_id)
    run_id = start_run(owner, project_id, evaluation["id"]).json()["run_id"]
    wait_for_run(owner, project_id, run_id)

    intruder = signed_in_account(client)
    for method, path, body in (
        ("GET", evaluations_path(project_id), None),
        ("POST", evaluations_path(project_id), {"dataset": DATASET}),
        ("GET", evaluations_path(project_id, evaluation["id"]), None),
        ("DELETE", evaluations_path(project_id, evaluation["id"]), None),
        ("POST", runs_path(project_id), {"evaluation_id": evaluation["id"]}),
        ("GET", runs_path(project_id), None),
        ("GET", runs_path(project_id, run_id), None),
        ("GET", runs_path(project_id, run_id, "/export"), None),
        ("POST", runs_path(project_id, run_id, "/cancel"), None),
    ):
        response = intruder.request(method, path, json=body)
        assert response.status_code == 404, (method, path)
        assert response.json()["error"]["code"] == "project_not_found"

    # Another project of the same user does not see them either.
    assert owner.request("GET", evaluations_path(other_project_id)).json()["data"] == []
    for response in (
        owner.request("GET", evaluations_path(other_project_id, evaluation["id"])),
        start_run(owner, other_project_id, evaluation["id"]),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "evaluation_not_found"
    for response in (
        owner.request("GET", runs_path(other_project_id, run_id)),
        owner.request("GET", runs_path(other_project_id, run_id, "/export")),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "evaluation_run_not_found"
    assert owner.request("GET", runs_path(project_id, "not-a-run-id")).status_code == 404


def test_a_run_completes_and_its_results_are_stored(lab):
    client, provider = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id)
    calls_before = len(provider.calls)

    started = start_run(account, project_id, evaluation["id"], prompt_version="v1")
    assert started.status_code == 202, started.text
    handle = started.json()
    assert len(handle["run_id"]) == 32 and handle["dataset_id"] == evaluation["id"]
    assert handle["cases_total"] == 3 and handle["state"] in {"running", "completed"}

    body = wait_for_run(account, project_id, handle["run_id"])
    assert body["state"] == "completed" and body["handle"]["persisted"] is True
    run = body["run"]
    assert run["status"] == "completed" and run["model"] == MODEL_ID
    assert run["prompt_version"] == "v1" and run["dataset_name"] == "Lab test dataset"
    assert [(r["name"], r["status"]) for r in run["results"]] == [
        ("Greets", "pass"),
        ("Wrong answer", "fail"),
        ("Yoruba prompt", "pass"),
    ]
    assert run["summary"]["counts"] == {"pass": 2, "fail": 1, "review": 0, "error": 0, "skipped": 0}
    assert run["summary"]["pass_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert run["results"][0]["attempts"][0]["output"] == "Scripted reply"
    assert len(provider.calls) - calls_before == 3
    assert run["methodology"], "the methodology text must travel with every result"

    run_uuid = uuid.UUID(hex=handle["run_id"])
    stored = db_fetch(
        "SELECT status, model, project_id, evaluation_id, started_at, finished_at "
        "FROM evaluation_runs WHERE id = $1",
        run_uuid,
    )
    assert len(stored) == 1 and stored[0]["status"] == "completed"
    assert str(stored[0]["project_id"]) == project_id
    assert str(stored[0]["evaluation_id"]) == evaluation["id"]
    assert stored[0]["finished_at"] >= stored[0]["started_at"]

    results = db_fetch(
        "SELECT r.status, r.output, r.latency_ms, r.error_code, r.request_log_id, c.position "
        "FROM evaluation_results r JOIN evaluation_cases c ON c.id = r.case_id "
        "WHERE r.run_id = $1 ORDER BY c.position",
        run_uuid,
    )
    assert [(row["position"], row["status"]) for row in results] == [
        (0, "pass"),
        (1, "fail"),
        (2, "pass"),
    ]
    assert all(row["output"] == "Scripted reply" and row["error_code"] is None for row in results)

    # Every attempt is a normal gateway request, logged as an evaluation call.
    logged = db_fetch(
        "SELECT source, user_id, api_key_id, endpoint, outcome, language FROM requests "
        "WHERE project_id = $1 ORDER BY created_at",
        uuid.UUID(project_id),
    )
    assert len(logged) == 3
    assert {row["source"] for row in logged} == {"evaluation"}
    assert {row["outcome"] for row in logged} == {"success"}
    assert all(row["user_id"] is None and row["api_key_id"] is None for row in logged)
    assert {row["endpoint"] for row in logged} == {"/api/v1/projects/{project_id}/evaluation-runs"}
    assert sorted(row["language"] for row in logged) == ["en", "en", "yo"]
    # Prompts are never stored with the request log.
    assert all("Say hello" not in str(value) for row in logged for value in row.values())


def test_a_finished_run_is_listed_exported_and_shown_on_the_dashboard(lab):
    client, _ = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id)
    run_id = start_run(account, project_id, evaluation["id"]).json()["run_id"]
    wait_for_run(account, project_id, run_id)

    listed = account.request("GET", runs_path(project_id)).json()["data"]
    assert [item["run_id"] for item in listed] == [run_id]
    item = listed[0]
    assert item["state"] == "completed" and item["dataset_name"] == "Lab test dataset"
    assert item["cases_total"] == 3 and item["counts"]["pass"] == 2 and item["model"] == MODEL_ID

    csv = account.request("GET", runs_path(project_id, run_id, "/export?format=csv"))
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["content-disposition"].startswith("attachment;")
    assert csv.headers["cache-control"] == "no-store"
    assert csv.content.startswith(b"\xef\xbb\xbf"), "Excel needs the byte-order mark"
    assert "Wrong answer" in csv.text and "Yoruba prompt" in csv.text

    exported = account.request("GET", runs_path(project_id, run_id, "/export?format=json"))
    assert exported.status_code == 200
    assert json.loads(exported.content)["run_id"] == run_id
    bad_format = account.request("GET", runs_path(project_id, run_id, "/export?format=xml"))
    assert bad_format.status_code == 422

    dashboard = account.request("GET", "/api/v1/dashboard/summary").json()
    recent = dashboard["recent_evaluation_runs"]
    assert [row["id"] for row in recent] == [run_id]
    assert recent[0]["project_id"] == project_id and recent[0]["status"] == "completed"
    assert recent[0]["evaluation_name"] == "Lab test dataset" and recent[0]["model"] == MODEL_ID


def test_the_database_store_returns_the_run_in_case_order_and_only_to_its_project(lab):
    client, _ = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id)
    run_id = start_run(account, project_id, evaluation["id"]).json()["run_id"]
    wait_for_run(account, project_id, run_id)

    store = DatabaseRunStore(client.app.state.session_factory)
    run = client.portal.call(store.get, project_id, run_id)
    assert run is not None and run.status == "completed" and run.project_id == project_id
    assert [(r.name, r.status) for r in run.results] == [
        ("Greets", "pass"),
        ("Wrong answer", "fail"),
        ("Yoruba prompt", "pass"),
    ]
    assert run.summary["counts"]["pass"] == 2 and run.methodology
    assert client.portal.call(store.get, str(uuid.uuid4()), run_id) is None
    assert client.portal.call(store.get, project_id, "b" * 32) is None
    assert client.portal.call(store.get, "not-a-uuid", run_id) is None

    summaries = client.portal.call(functools.partial(store.list, project_id, limit=10))
    assert [s["run_id"] for s in summaries] == [run_id]
    assert summaries[0]["status"] == "completed" and summaries[0]["summary"]["total"] == 3
    assert client.portal.call(functools.partial(store.list, str(uuid.uuid4()), limit=10)) == []


def test_a_run_is_refused_while_the_model_is_away_or_unknown(lab):
    client, provider = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id)
    calls_before = len(provider.calls)

    provider.model_status = "unavailable"
    try:
        away = start_run(account, project_id, evaluation["id"])
    finally:
        provider.model_status = "available"
    assert away.status_code == 503 and away.json()["error"]["code"] == "provider_unavailable"

    unknown = start_run(account, project_id, evaluation["id"], model="gpt-4o")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "model_not_found"

    assert account.request("GET", runs_path(project_id)).json()["data"] == []
    assert len(provider.calls) == calls_before


def test_a_run_can_be_cancelled_and_keeps_what_it_finished(lab):
    client, provider = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id, SIX_CASES)

    provider.delay_seconds = 0.4
    try:
        run_id = start_run(account, project_id, evaluation["id"]).json()["run_id"]
        cancelled = account.request("POST", runs_path(project_id, run_id, "/cancel"))
        assert cancelled.status_code == 202 and cancelled.json() == {"cancelling": True}
        body = wait_for_run(account, project_id, run_id)
    finally:
        provider.delay_seconds = 0.0

    assert body["state"] == "cancelled" and body["run"]["status"] == "cancelled"
    statuses = [result["status"] for result in body["run"]["results"]]
    assert len(statuses) == 6 and "skipped" in statuses
    assert body["run"]["summary"]["counts"]["skipped"] == statuses.count("skipped")

    again = account.request("POST", runs_path(project_id, run_id, "/cancel"))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "evaluation_run_not_running"
    missing = account.request("POST", runs_path(project_id, "c" * 32, "/cancel"))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "evaluation_run_not_found"


def test_a_project_runs_one_evaluation_at_a_time(lab):
    client, provider = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id, SIX_CASES)

    provider.delay_seconds = 0.4
    try:
        first = start_run(account, project_id, evaluation["id"])
        assert first.status_code == 202
        second = start_run(account, project_id, evaluation["id"])
        assert second.status_code == 429 and second.json()["error"]["code"] == "rate_limited"
        run_id = first.json()["run_id"]
        assert account.request("POST", runs_path(project_id, run_id, "/cancel")).status_code == 202
        wait_for_run(account, project_id, run_id)
    finally:
        provider.delay_seconds = 0.0


def test_an_unfinished_run_cannot_be_exported_yet(lab):
    client, provider = lab
    account = signed_in_account(client)
    project_id = new_project(account)
    evaluation = create_dataset(account, project_id, SIX_CASES)

    provider.delay_seconds = 0.4
    try:
        run_id = start_run(account, project_id, evaluation["id"]).json()["run_id"]
        early = account.request("GET", runs_path(project_id, run_id, "/export?format=csv"))
        assert early.status_code == 409
        assert early.json()["error"]["code"] == "evaluation_run_not_finished"
        account.request("POST", runs_path(project_id, run_id, "/cancel"))
        wait_for_run(account, project_id, run_id)
    finally:
        provider.delay_seconds = 0.0


def test_starting_runs_is_rate_limited_per_user(migrated_database):
    app = create_app(make_settings(eval_runs_per_hour=1))
    with TestClient(app) as limited:
        install_scripted_provider(app)
        account = Account(limited)
        assert account.register().status_code == 201
        project_id = new_project(account)
        evaluation = create_dataset(account, project_id)
        first = start_run(account, project_id, evaluation["id"])
        assert first.status_code == 202
        wait_for_run(account, project_id, first.json()["run_id"])
        blocked = start_run(account, project_id, evaluation["id"])
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "evaluation_rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


def test_a_project_can_hold_only_so_many_datasets(migrated_database):
    app = create_app(make_settings(eval_max_evaluations_per_project=1))
    with TestClient(app) as limited:
        account = Account(limited)
        assert account.register().status_code == 201
        project_id = new_project(account)
        first = create_dataset(account, project_id)
        refused = account.request("POST", evaluations_path(project_id), json={"dataset": DATASET})
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "evaluation_limit_reached"
        # Archiving one makes room again.
        assert account.request("DELETE", evaluations_path(project_id, first["id"])).status_code == 204
        create_dataset(account, project_id)
