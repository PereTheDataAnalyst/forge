"""Conversions between the evaluation library's objects and the platform's database rows.

These are pure functions with no framework imports, so they run without a database or a web
server. The library's data fits the existing tables without a migration:

- `evaluations` has one row per dataset (name, description).
- `evaluation_cases` has one row per case. `expected` holds what has no column of its own: the
  case's display name and key, its checks, repeat count, token and temperature limits, tags,
  and the dataset version.
- `evaluation_runs` has one row per finished run. `summary` holds the dataset name and version,
  the prompt version, the run configuration, the metrics summary and the methodology text.
- `evaluation_results` has one row per case. `output` is the first successful reply,
  `latency_ms` the slowest successful attempt and `error_code` the case error. `checks` holds
  the whole case result as one JSON object (attempts, check results, error message), so the
  screen and the exports can be rebuilt exactly.
"""

import math
import uuid
from datetime import UTC, datetime
from typing import Any

from forge_integrations.evaluation.models import (
    Attempt,
    Case,
    CaseResult,
    Check,
    CheckResult,
    Dataset,
    Run,
)
from forge_integrations.evaluation.validation import EvaluationLimits

DEFAULT_DATASET_VERSION = "1"

# These match the column sizes of the `evaluations` table (name 120, description 500).
MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 500


def dataset_limits(max_cases: int) -> EvaluationLimits:
    """Validation limits for datasets that will be stored in the database."""
    return EvaluationLimits(
        max_cases=max_cases,
        max_name_chars=MAX_NAME_CHARS,
        max_description_chars=MAX_DESCRIPTION_CHARS,
    )


# -- JSON safety ---------------------------------------------------------------------------


def clean_text(value: str | None) -> str | None:
    """PostgreSQL text and JSONB cannot hold the NUL character, and a model may emit one."""
    return None if value is None else value.replace("\x00", "")


def clean_json(value: Any) -> Any:
    """Make a value storable as JSONB: no NUL characters, no NaN and no infinity."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(clean_text(str(key))): clean_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [clean_json(item) for item in value]
    return value


# -- Timestamps ----------------------------------------------------------------------------


def parse_timestamp(text: str) -> datetime:
    """Parse the library's `2026-10-16T09:30:00.123Z` style timestamps."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def format_timestamp(moment: datetime | None) -> str:
    """The same style the library writes, from a database datetime. Empty when unknown."""
    if moment is None:
        return ""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


# -- Datasets ------------------------------------------------------------------------------


def case_expected(case: Case, dataset_version: str) -> dict[str, Any]:
    """Everything about a case that has no column of its own."""
    return clean_json(
        {
            "key": case.id,
            "name": case.name,
            "checks": [check.to_dict() for check in case.checks],
            "repeat": case.repeat,
            "max_tokens": case.max_tokens,
            "temperature": case.temperature,
            "tags": list(case.tags),
            "dataset_version": dataset_version,
        }
    )


def dataset_to_case_rows(dataset: Dataset) -> list[dict[str, Any]]:
    """Column values for one `evaluation_cases` row per case, in order."""
    return [
        {
            "position": position,
            "category": case.category,
            "prompt": case.prompt,
            "system_prompt": case.system,
            "language": case.language,
            "expected": case_expected(case, dataset.version),
        }
        for position, case in enumerate(dataset.cases)
    ]


def _check_from_dict(item: dict[str, Any]) -> Check:
    return Check(type=item["type"], params={k: v for k, v in item.items() if k != "type"})


def rows_to_dataset(evaluation: Any, case_rows: list[Any]) -> Dataset:
    """Rebuild the library's Dataset from an `evaluations` row and its case rows.

    Case ids are the database row ids, so every result can be tied to its case row. The
    human-readable key from the dataset file is kept in `expected["key"]`.
    """
    ordered = sorted(case_rows, key=lambda row: row.position)
    cases = []
    for row in ordered:
        spec = row.expected or {}
        cases.append(
            Case(
                id=str(row.id),
                name=str(spec.get("name") or spec.get("key") or f"Case {row.position + 1}"),
                prompt=row.prompt,
                checks=tuple(_check_from_dict(item) for item in spec.get("checks", [])),
                category=row.category,
                system=row.system_prompt,
                language=row.language,
                repeat=int(spec.get("repeat", 1)),
                max_tokens=spec.get("max_tokens"),
                temperature=spec.get("temperature"),
                tags=tuple(spec.get("tags", [])),
            )
        )
    version = DEFAULT_DATASET_VERSION
    if ordered:
        version = str((ordered[0].expected or {}).get("dataset_version", DEFAULT_DATASET_VERSION))
    return Dataset(
        id=str(evaluation.id),
        name=evaluation.name,
        description=evaluation.description or "",
        version=version,
        cases=tuple(cases),
    )


def case_to_dict(row: Any) -> dict[str, Any]:
    """A case as shown on screen."""
    spec = row.expected or {}
    return {
        "id": str(row.id),
        "position": row.position,
        "key": spec.get("key"),
        "name": spec.get("name") or spec.get("key"),
        "category": row.category,
        "language": row.language,
        "prompt": row.prompt,
        "system": row.system_prompt,
        "checks": spec.get("checks", []),
        "repeat": spec.get("repeat", 1),
        "max_tokens": spec.get("max_tokens"),
        "temperature": spec.get("temperature"),
        "tags": spec.get("tags", []),
    }


def evaluation_to_dict(evaluation: Any, case_count: int) -> dict[str, Any]:
    return {
        "id": str(evaluation.id),
        "name": evaluation.name,
        "description": evaluation.description,
        "case_count": case_count,
        "created_at": evaluation.created_at.isoformat(),
    }


# -- Runs ----------------------------------------------------------------------------------


def _first_output(result: CaseResult) -> str | None:
    for attempt in result.attempts:
        if attempt.ok and attempt.output is not None:
            return clean_text(attempt.output)
    return None


def _slowest_latency(result: CaseResult) -> int | None:
    latencies = [attempt.latency_ms for attempt in result.attempts if attempt.ok]
    return int(round(max(latencies))) if latencies else None


def run_to_rows(run: Run) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Column values for one `evaluation_runs` row and one `evaluation_results` row per case.

    Raises ValueError when an id is not a UUID. Ids created by this module always are.
    """
    run_id = uuid.UUID(hex=run.run_id)
    if run.project_id is None:
        raise ValueError("A stored run needs a project id.")
    run_row = {
        "id": run_id,
        "evaluation_id": uuid.UUID(run.dataset_id),
        "project_id": uuid.UUID(run.project_id),
        "model": (run.model or "")[:120],
        "status": run.status,
        "started_at": parse_timestamp(run.started_at),
        "finished_at": parse_timestamp(run.finished_at),
        "summary": clean_json(
            {
                "dataset_name": run.dataset_name,
                "dataset_version": run.dataset_version,
                "prompt_version": run.prompt_version,
                "config": run.config,
                "summary": run.summary,
                "methodology": list(run.methodology),
            }
        ),
    }
    result_rows = [
        {
            "run_id": run_id,
            "case_id": uuid.UUID(result.case_id),
            "status": result.status,
            "output": _first_output(result),
            "latency_ms": _slowest_latency(result),
            "error_code": result.error_code[:64] if result.error_code else None,
            "checks": clean_json(result.to_dict()),
            "request_log_id": None,
        }
        for result in run.results
    ]
    return run_row, result_rows


def case_result_from_dict(data: dict[str, Any]) -> CaseResult:
    """Inverse of `CaseResult.to_dict()`."""
    return CaseResult(
        case_id=str(data["case_id"]),
        name=str(data["name"]),
        category=str(data["category"]),
        language=data.get("language"),
        status=str(data["status"]),
        attempts=[Attempt(**item) for item in data.get("attempts", [])],
        checks=[CheckResult(**item) for item in data.get("checks", [])],
        error_code=data.get("error_code"),
        error_message=data.get("error_message"),
        partial_error_count=int(data.get("partial_error_count", 0)),
    )


def rows_to_run(run_row: Any, result_rows: list[Any]) -> Run:
    """Rebuild the library's Run from stored rows. Result rows must already be in case order."""
    doc = run_row.summary or {}
    return Run(
        run_id=run_row.id.hex,
        dataset_id=str(run_row.evaluation_id),
        dataset_name=str(doc.get("dataset_name", "")),
        dataset_version=str(doc.get("dataset_version", DEFAULT_DATASET_VERSION)),
        model=run_row.model or None,
        prompt_version=doc.get("prompt_version"),
        project_id=str(run_row.project_id),
        status=run_row.status,
        started_at=format_timestamp(run_row.started_at),
        finished_at=format_timestamp(run_row.finished_at),
        config=dict(doc.get("config") or {}),
        results=[case_result_from_dict(row.checks) for row in result_rows],
        summary=dict(doc.get("summary") or {}),
        methodology=tuple(doc.get("methodology") or ()),
    )


def run_row_summary(run_row: Any) -> dict[str, Any]:
    """The list view of a stored run. Same keys as the library's `run_summary`."""
    doc = run_row.summary or {}
    return {
        "run_id": run_row.id.hex,
        "dataset_id": str(run_row.evaluation_id),
        "dataset_name": doc.get("dataset_name"),
        "dataset_version": doc.get("dataset_version"),
        "model": run_row.model or None,
        "prompt_version": doc.get("prompt_version"),
        "status": run_row.status,
        "started_at": format_timestamp(run_row.started_at),
        "finished_at": format_timestamp(run_row.finished_at),
        "summary": doc.get("summary") or {},
    }


def run_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """One uniform shape for the run list, whether the item is a run in progress (a library
    RunHandle) or a stored run (a library run summary)."""
    live = "state" in item
    summary = item.get("summary") or {}
    total = item.get("cases_total") if live else summary.get("total")
    return {
        "run_id": item["run_id"],
        "dataset_id": item.get("dataset_id"),
        "dataset_name": item.get("dataset_name"),
        "state": item["state"] if live else item.get("status"),
        "model": item.get("model"),
        "prompt_version": item.get("prompt_version"),
        "started_at": item.get("created_at") if live else item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "cases_total": total,
        "cases_done": item.get("cases_done") if live else total,
        "pass_rate": summary.get("pass_rate"),
        "counts": summary.get("counts") or None,
    }
