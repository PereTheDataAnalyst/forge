"""Run export. CSV cells that could be interpreted as formulas are neutralised."""

from __future__ import annotations

import csv
import io
import json

from .models import CaseResult, Run

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
MAX_CSV_OUTPUT_CHARS = 2000

CSV_COLUMNS = (
    "run_id",
    "dataset_name",
    "dataset_version",
    "model",
    "prompt_version",
    "case_id",
    "case_name",
    "category",
    "language",
    "status",
    "checks_passed",
    "checks_failed",
    "checks_review",
    "failed_check_types",
    "attempts",
    "attempts_failed",
    "max_latency_ms",
    "error_code",
    "first_output",
    "first_output_truncated",
)


def csv_safe(value: object) -> object:
    """Prefix a single quote when a text cell starts like a spreadsheet formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def export_json(run: Run) -> str:
    return json.dumps(run.to_dict(), ensure_ascii=False, indent=2)


def _row(run: Run, r: CaseResult) -> list[object]:
    good = [a for a in r.attempts if a.ok]
    first = good[0].output if good and good[0].output is not None else ""
    return [
        run.run_id,
        run.dataset_name,
        run.dataset_version,
        run.model or "",
        run.prompt_version or "",
        r.case_id,
        r.name,
        r.category,
        r.language or "",
        r.status,
        sum(1 for c in r.checks if c.passed is True),
        sum(1 for c in r.checks if c.passed is False),
        sum(1 for c in r.checks if c.passed is None),
        ";".join(c.type for c in r.checks if c.passed is False),
        len(r.attempts),
        sum(1 for a in r.attempts if not a.ok),
        round(max((a.latency_ms for a in good), default=0.0), 2) if good else "",
        r.error_code or "",
        first[:MAX_CSV_OUTPUT_CHARS],
        len(first) > MAX_CSV_OUTPUT_CHARS or any(a.output_truncated for a in good),
    ]


def export_csv(run: Run) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for result in run.results:
        writer.writerow([csv_safe(cell) for cell in _row(run, result)])
    return buffer.getvalue()
