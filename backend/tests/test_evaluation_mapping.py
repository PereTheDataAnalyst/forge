"""Evaluation module: conversions between the library's objects and database rows.

Pure tests with no database. They run the real evaluation library on its real starter
datasets, push the results through the same conversions the database store uses, and check
that nothing is lost or changed on the way back.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from forge_integrations.config import EvaluationSettings
from forge_integrations.errors import ForgeError
from forge_integrations.evaluation import load_starter_dataset, starter_dataset_ids
from forge_integrations.evaluation.models import RunConfig
from forge_integrations.evaluation.runner import EvaluationRunner
from forge_integrations.evaluation.service import InMemoryRunStore, RunManager
from forge_integrations.evaluation.validation import validate_dataset
from forge_integrations.ports import GenerationResult, Usage

from app.modules.evaluation.mapping import (
    case_to_dict,
    clean_json,
    dataset_limits,
    dataset_to_case_rows,
    evaluation_to_dict,
    format_timestamp,
    parse_timestamp,
    rows_to_dataset,
    rows_to_run,
    run_list_item,
    run_row_summary,
    run_to_rows,
)

PROJECT_ID = "5b1f0b0e-7d6e-4c58-9d0e-2f3c4a5b6c7d"
RUN_ID = "a" * 32
LIMITS = dataset_limits(100)


class EchoGateway:
    """A model stand-in for these tests. Its replies contain a NUL character on purpose: a
    real model can emit one, and PostgreSQL cannot store it."""

    async def generate(self, request):
        prompt = request.messages[-1].content
        return GenerationResult(
            text=f"Reply\x00 to {prompt[:40]}",
            model="NCAIR1/N-ATLaS",
            usage=Usage(3, 4, 7),
            latency_ms=12.5,
            finish_reason="stop",
        )


def database_rows(dataset):
    """What the database would hold for a dataset, as plain objects with the same attributes."""
    evaluation = SimpleNamespace(
        id=uuid.uuid4(),
        name=dataset.name,
        description=dataset.description or None,
        created_at=datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
    )
    cases = [SimpleNamespace(id=uuid.uuid4(), **values) for values in dataset_to_case_rows(dataset)]
    return evaluation, cases


def run_dataset(dataset, *, run_id=RUN_ID):
    runner = EvaluationRunner(EchoGateway())
    config = RunConfig(model="NCAIR1/N-ATLaS", prompt_version="v7", max_concurrency=2)
    return asyncio.run(runner.run(dataset, config, project_id=PROJECT_ID, run_id=run_id))


def stored_rows(run):
    """Push a run through the conversion the store uses and wrap the values as row objects."""
    run_values, result_values = run_to_rows(run)
    run_row = SimpleNamespace(**run_values)
    return run_row, [SimpleNamespace(**values) for values in result_values]


def test_every_starter_dataset_survives_a_round_trip_through_rows():
    for starter_id in starter_dataset_ids():
        original = load_starter_dataset(starter_id, limits=LIMITS)
        evaluation, cases = database_rows(original)
        rebuilt = rows_to_dataset(evaluation, list(reversed(cases)))  # order comes from position

        assert rebuilt.id == str(evaluation.id) and rebuilt.name == original.name
        assert rebuilt.version == original.version
        assert len(rebuilt.cases) == len(original.cases)
        for before, after in zip(original.cases, rebuilt.cases, strict=True):
            assert after.name == before.name and after.prompt == before.prompt
            assert after.category == before.category and after.language == before.language
            assert after.system == before.system and after.repeat == before.repeat
            assert after.max_tokens == before.max_tokens
            assert after.temperature == before.temperature
            assert after.tags == before.tags
            assert [c.to_dict() for c in after.checks] == [c.to_dict() for c in before.checks]

        # Behaviour, not just shape: both datasets must give the same verdicts.
        verdicts = [
            [(r.name, r.status, [(c.type, c.passed) for c in r.checks]) for r in run.results]
            for run in (run_dataset(original), run_dataset(rebuilt))
        ]
        assert verdicts[0] == verdicts[1], starter_id


def test_a_finished_run_survives_a_round_trip_through_rows():
    original_dataset = load_starter_dataset("starter-nigerian-languages", limits=LIMITS)
    evaluation, case_rows = database_rows(original_dataset)
    dataset = rows_to_dataset(evaluation, case_rows)
    run = run_dataset(dataset)
    run_row, result_rows = stored_rows(run)
    run_row.evaluation_id = uuid.UUID(dataset.id)

    rebuilt = rows_to_run(run_row, result_rows)

    assert rebuilt.to_dict() == clean_json(run.to_dict())
    assert rebuilt.run_id == RUN_ID and rebuilt.project_id == PROJECT_ID
    assert rebuilt.prompt_version == "v7" and rebuilt.dataset_name == dataset.name
    assert [r.case_id for r in rebuilt.results] == [str(row.id) for row in case_rows]


def test_stored_values_are_valid_for_postgresql():
    dataset = load_starter_dataset("starter-general-checks", limits=LIMITS)
    evaluation, case_rows = database_rows(dataset)
    run = run_dataset(rows_to_dataset(evaluation, case_rows))
    assert any("\x00" in (a.output or "") for r in run.results for a in r.attempts)

    run_values, result_values = run_to_rows(run)

    # JSONB rejects NUL characters and the NaN and Infinity tokens.
    assert "\\u0000" not in json.dumps(run_values["summary"])
    assert json.dumps(run_values["summary"], allow_nan=False)
    for values in result_values:
        assert "\\u0000" not in json.dumps(values["checks"])
        assert json.dumps(values["checks"], allow_nan=False)
        assert values["output"] is None or "\x00" not in values["output"]
        assert len(values["status"]) <= 16
        assert values["error_code"] is None or len(values["error_code"]) <= 64
    assert len(run_values["model"]) <= 120 and len(run_values["status"]) <= 16
    assert run_values["started_at"].tzinfo is not None
    assert run_values["finished_at"] >= run_values["started_at"]
    assert [v["case_id"] for v in result_values] == [row.id for row in case_rows]


def test_a_result_row_holds_the_first_good_output_and_the_slowest_good_latency():
    dataset = load_starter_dataset("starter-general-checks", limits=LIMITS)
    evaluation, case_rows = database_rows(dataset)
    run = run_dataset(rows_to_dataset(evaluation, case_rows))
    _, result_values = run_to_rows(run)
    for result, values in zip(run.results, result_values, strict=True):
        good = [a for a in result.attempts if a.ok]
        assert (values["output"] or "") == (good[0].output.replace("\x00", "") if good else "")
        assert values["latency_ms"] == (round(max(a.latency_ms for a in good)) if good else None)


def test_clean_json_removes_nul_characters_and_non_finite_numbers():
    dirty = {
        "text\x00key": ["a\x00b", ("c", 1, True, None)],
        "n": float("nan"),
        "inf": float("inf"),
        "ok": 1.5,
        "nested": {"deep": "x\x00"},
    }
    clean = clean_json(dirty)
    assert clean == {
        "textkey": ["ab", ["c", 1, True, None]],
        "n": None,
        "inf": None,
        "ok": 1.5,
        "nested": {"deep": "x"},
    }
    json.dumps(clean, allow_nan=False)


def test_timestamps_round_trip_in_the_library_format():
    for text in ("2026-10-16T09:30:00.123Z", "2026-12-31T23:59:59.000Z"):
        assert format_timestamp(parse_timestamp(text)) == text
    assert parse_timestamp("2026-10-16T09:30:00.123Z").tzinfo is not None
    assert format_timestamp(None) == ""


def test_the_run_list_has_one_shape_for_running_and_finished_runs():
    document = {
        "name": "One case",
        "cases": [
            {
                "id": "c1",
                "name": "Greeting",
                "category": "general",
                "prompt": "Say hello.",
                "checks": [{"type": "not_empty"}],
            }
        ],
    }

    class GatedGateway:
        def __init__(self):
            self.release = asyncio.Event()

        async def generate(self, request):
            await self.release.wait()
            return GenerationResult(
                text="Hello!", model="m", usage=Usage(1, 1, 2), latency_ms=5.0, finish_reason="stop"
            )

    async def scenario():
        gateway = GatedGateway()
        manager = RunManager(
            EvaluationRunner(gateway),
            InMemoryRunStore(),
            EvaluationSettings(
                max_cases=100,
                max_concurrency=1,
                call_timeout_seconds=10.0,
                max_concurrent_runs_per_project=1,
            ),
        )
        handle = await manager.start(PROJECT_ID, validate_dataset(document), RunConfig(model="m"))
        running = await manager.list_runs(PROJECT_ID, limit=10)
        gateway.release.set()
        for _ in range(300):
            if manager.status(PROJECT_ID, handle.run_id).state != "running":
                break
            await asyncio.sleep(0.01)
        finished = await manager.list_runs(PROJECT_ID, limit=10)
        await manager.shutdown()
        return handle, running, finished

    handle, running, finished = asyncio.run(scenario())

    live = run_list_item(running[0])
    assert live["run_id"] == handle.run_id and live["state"] == "running"
    assert live["cases_total"] == 1 and live["cases_done"] == 0
    assert live["pass_rate"] is None and live["counts"] is None

    done = run_list_item(finished[0])
    assert done["run_id"] == handle.run_id and done["state"] == "completed"
    assert done["cases_total"] == 1 and done["cases_done"] == 1
    assert done["pass_rate"] == 1.0 and done["counts"]["pass"] == 1
    assert set(live) == set(done)


def test_a_stored_run_lists_like_the_librarys_own_summary():
    dataset = load_starter_dataset("starter-general-checks", limits=LIMITS)
    evaluation, case_rows = database_rows(dataset)
    run = run_dataset(rows_to_dataset(evaluation, case_rows))
    run_row, _ = stored_rows(run)
    run_row.evaluation_id = uuid.UUID(run.dataset_id)

    summary = run_row_summary(run_row)

    assert summary["run_id"] == RUN_ID and summary["status"] == "completed"
    assert summary["dataset_name"] == dataset.name and summary["prompt_version"] == "v7"
    assert summary["summary"] == clean_json(run.summary)
    item = run_list_item(summary)
    assert item["state"] == "completed" and item["cases_total"] == len(dataset.cases)


def test_evaluation_and_case_shapes_shown_on_screen():
    dataset = load_starter_dataset("starter-nigerian-languages", limits=LIMITS)
    evaluation, case_rows = database_rows(dataset)
    shown = evaluation_to_dict(evaluation, len(case_rows))
    assert shown["case_count"] == len(dataset.cases) and shown["name"] == dataset.name
    assert shown["created_at"] == "2026-10-16T09:00:00+00:00"
    first = case_to_dict(case_rows[0])
    original = dataset.cases[0]
    assert first["key"] == original.id and first["name"] == original.name
    assert first["prompt"] == original.prompt and first["checks"][0]["type"]
    assert first["position"] == 0 and first["id"] == str(case_rows[0].id)


def test_dataset_limits_match_the_database_columns():
    base = {
        "name": "n",
        "cases": [
            {"id": "c1", "name": "x", "category": "general", "prompt": "Hi", "checks": [{"type": "not_empty"}]}
        ],
    }
    validate_dataset({**base, "name": "n" * 120, "description": "d" * 500}, limits=LIMITS)
    for field, size in (("name", 121), ("description", 501)):
        try:
            validate_dataset({**base, field: "x" * size}, limits=LIMITS)
        except ForgeError as error:
            assert error.http_status == 422
        else:
            raise AssertionError(f"a {field} of {size} characters should not validate")
