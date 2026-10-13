"""Run metrics and run comparison.

Every number is computed from the recorded attempts and check results of a run.
Definitions (also in docs/evaluation.md):

  completed            cases whose model call succeeded and whose checks ran
  pass_rate            pass / completed. It is the share of developer-defined checks
                       that held. It is not model accuracy.
  check_failure_rate   fail / completed
  review_rate          review / completed (a heuristic check could not decide)
  error_rate           error / (cases that were not skipped): provider failures
  attempts.failure_rate failed attempts / attempts, the provider failure rate
  latency              nearest-rank percentiles over successful attempts, measured
                       by the runner around the gateway call
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Sequence

from .models import (
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_REVIEW,
    STATUS_SKIPPED,
    STATUSES,
    CaseResult,
    Run,
)
from .textnorm import similarity

METHODOLOGY: tuple[str, ...] = (
    "Scores come only from checks the developer defined. There is no accuracy score and no model-based judge.",
    "pass_rate is the share of completed cases whose checks all held. It is not model accuracy.",
    "language_match is a heuristic (function words and orthographic markers). It reports review when unsure.",
    "consistency is the mean pairwise token similarity (difflib ratio) between repeated attempts of the same prompt.",
    "Latency is measured by the runner around the gateway call and summarised with nearest-rank percentiles.",
    "error means the model call failed, so the case was not evaluated. Errors are reported separately from failures.",
)


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile. ``q`` is in (0, 1]."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = max(1, math.ceil(q * n))
    return sorted_values[rank - 1]


def latency_stats(values: Iterable[float]) -> dict[str, Any]:
    data = sorted(values)
    if not data:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(data),
        "min": round(data[0], 2),
        "mean": round(sum(data) / len(data), 2),
        "p50": round(percentile(data, 0.50), 2),  # type: ignore[arg-type]
        "p95": round(percentile(data, 0.95), 2),  # type: ignore[arg-type]
        "max": round(data[-1], 2),
    }


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def _group_summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    for r in results:
        counts[r.status] += 1
    completed = counts[STATUS_PASS] + counts[STATUS_FAIL] + counts[STATUS_REVIEW]
    not_skipped = len(results) - counts[STATUS_SKIPPED]
    return {
        "total": len(results),
        "counts": counts,
        "completed": completed,
        "pass_rate": _ratio(counts[STATUS_PASS], completed),
        "check_failure_rate": _ratio(counts[STATUS_FAIL], completed),
        "review_rate": _ratio(counts[STATUS_REVIEW], completed),
        "error_rate": _ratio(counts[STATUS_ERROR], not_skipped),
    }


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    summary = _group_summary(results)

    by_category: dict[str, Any] = {}
    for category in sorted({r.category for r in results}):
        by_category[category] = _group_summary([r for r in results if r.category == category])
    summary["by_category"] = by_category

    by_language: dict[str, Any] = {}
    for language in sorted({r.language or "unspecified" for r in results}):
        group = [r for r in results if (r.language or "unspecified") == language]
        by_language[language] = _group_summary(group)
    summary["by_language"] = by_language

    attempts = [a for r in results for a in r.attempts]
    failed = [a for a in attempts if not a.ok]
    summary["attempts"] = {
        "total": len(attempts),
        "failed": len(failed),
        "failure_rate": _ratio(len(failed), len(attempts)),
        "errors_by_code": dict(Counter(a.error_code or "unknown" for a in failed)),
    }
    summary["latency_ms"] = latency_stats(a.latency_ms for a in attempts if a.ok)

    checks: dict[str, dict[str, int]] = {}
    consistency_values: list[float] = []
    for r in results:
        for c in r.checks:
            bucket = checks.setdefault(c.type, {"pass": 0, "fail": 0, "review": 0})
            bucket["review" if c.passed is None else "pass" if c.passed else "fail"] += 1
            if c.type == "consistency" and c.passed is not None:
                value = c.evidence.get("mean_similarity")
                if isinstance(value, (int, float)):
                    consistency_values.append(float(value))
    summary["checks"] = checks
    summary["consistency"] = (
        {"cases": len(consistency_values), "mean_similarity": round(sum(consistency_values) / len(consistency_values), 4)}
        if consistency_values
        else None
    )
    return summary


_RANK = {STATUS_PASS: 3, STATUS_REVIEW: 2, STATUS_FAIL: 1, STATUS_ERROR: 0}


def _first_output(result: CaseResult) -> str | None:
    for attempt in result.attempts:
        if attempt.ok and attempt.output is not None:
            return attempt.output
    return None


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 4)


def compare_runs(a: Run, b: Run) -> dict[str, Any]:
    """Compare run ``b`` against baseline run ``a`` case by case."""
    index_b = {r.case_id: r for r in b.results}
    index_a = {r.case_id: r for r in a.results}
    cases: list[dict[str, Any]] = []
    tally: Counter[str] = Counter()

    for ra in a.results:
        rb = index_b.get(ra.case_id)
        if rb is None:
            change = "missing_in_b"
            entry: dict[str, Any] = {"case_id": ra.case_id, "name": ra.name, "a_status": ra.status, "b_status": None}
        else:
            if STATUS_SKIPPED in (ra.status, rb.status):
                change = "not_comparable"
            elif ra.status == rb.status:
                change = "unchanged"
            else:
                change = "improvement" if _RANK[rb.status] > _RANK[ra.status] else "regression"
            oa, ob = _first_output(ra), _first_output(rb)
            entry = {
                "case_id": ra.case_id,
                "name": ra.name,
                "a_status": ra.status,
                "b_status": rb.status,
                "output_similarity": None if oa is None or ob is None else round(similarity(oa, ob), 4),
            }
        entry["change"] = change
        tally[change] += 1
        cases.append(entry)

    for rb in b.results:
        if rb.case_id not in index_a:
            cases.append({"case_id": rb.case_id, "name": rb.name, "a_status": None, "b_status": rb.status, "change": "missing_in_a"})
            tally["missing_in_a"] += 1

    sa, sb = a.summary, b.summary
    return {
        "baseline_run_id": a.run_id,
        "compared_run_id": b.run_id,
        "same_dataset": a.dataset_id == b.dataset_id,
        "baseline": {"model": a.model, "prompt_version": a.prompt_version},
        "compared": {"model": b.model, "prompt_version": b.prompt_version},
        "tally": dict(tally),
        "delta": {
            "pass_rate": _delta(sa.get("pass_rate"), sb.get("pass_rate")),
            "error_rate": _delta(sa.get("error_rate"), sb.get("error_rate")),
            "latency_p50_ms": _delta(sa["latency_ms"].get("p50"), sb["latency_ms"].get("p50")),
            "latency_p95_ms": _delta(sa["latency_ms"].get("p95"), sb["latency_ms"].get("p95")),
        },
        "cases": cases,
    }
