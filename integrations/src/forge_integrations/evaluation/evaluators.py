"""Deterministic check implementations.

Each check receives validated parameters and a ``CheckContext`` and returns a
``CheckResult``. ``passed`` is True or False, or None when a heuristic check cannot
decide. There are no model-based judges and no accuracy scores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Mapping

from ..security.redaction import find_secret_labels
from .language import detect_language
from .models import Case, Check, CheckResult
from .textnorm import normalise, similarity, words

_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")
_NG_PHONE_RE = re.compile(r"(?<!\d)(?:\+?234|0)[789][01]\d{8}(?!\d)")
_E164_RE = re.compile(r"(?<![\w+])\+\d{10,15}(?!\d)")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


@dataclass(frozen=True)
class CheckContext:
    case: Case
    outputs: tuple[str, ...]  # outputs of successful attempts, in order
    latencies_ms: tuple[float, ...]  # latencies of successful attempts, in order

    @property
    def primary(self) -> str:
        return self.outputs[0]


def _norm_kwargs(params: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "ignore_case": params.get("ignore_case", True),
        "ignore_diacritics": params.get("ignore_diacritics", False),
        "normalize_whitespace": params.get("normalize_whitespace", True),
        "strip_punctuation": params.get("strip_punctuation", False),
    }


def _result(ctype: str, passed: bool | None, detail: str, **evidence: Any) -> CheckResult:
    return CheckResult(type=ctype, passed=passed, detail=detail, evidence=dict(evidence))


def _exact_match(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    kw = _norm_kwargs(p)
    ok = normalise(ctx.primary, **kw) == normalise(p["expected"], **kw)
    return _result("exact_match", ok, "Output equals the expected text after normalisation." if ok else "Output differs from the expected text after normalisation.")


def _contains(ctype: str, p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    kw = _norm_kwargs(p)
    haystack = normalise(ctx.primary, **kw)
    present = [v for v in p["values"] if normalise(v, **kw) in haystack]
    absent = [v for v in p["values"] if v not in present]
    if ctype == "contains_all":
        ok = not absent
        return _result(ctype, ok, "All expected values are present." if ok else f"{len(absent)} expected value(s) are missing.", missing=absent)
    if ctype == "contains_any":
        ok = bool(present)
        return _result(ctype, ok, "At least one expected value is present." if ok else "None of the expected values are present.", found=present)
    ok = not present
    return _result(ctype, ok, "No forbidden value is present." if ok else f"{len(present)} forbidden value(s) are present.", found=present)


def _starts_ends(ctype: str, p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    kw = _norm_kwargs(p)
    text, value = normalise(ctx.primary, **kw), normalise(p["value"], **kw)
    ok = text.startswith(value) if ctype == "starts_with" else text.endswith(value)
    return _result(ctype, ok, "Output matches." if ok else "Output does not match.")


def _length_between(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    n = len(ctx.primary.strip())
    lo, hi = p.get("min_chars"), p.get("max_chars")
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    return _result("length_between", ok, f"Output has {n} characters.", chars=n, min_chars=lo, max_chars=hi)


def _word_count_between(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    n = len(words(ctx.primary))
    lo, hi = p.get("min_words"), p.get("max_words")
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    return _result("word_count_between", ok, f"Output has {n} words.", words=n, min_words=lo, max_words=hi)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _json_valid(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    text = ctx.primary.strip()
    if p.get("allow_code_fence", True):
        match = _FENCE_RE.match(text)
        if match:
            text = match.group(1).strip()
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _result("json_valid", False, "Output is not valid JSON.")
    required = p.get("required_keys") or []
    if not required:
        return _result("json_valid", True, "Output is valid JSON.")
    if not isinstance(parsed, dict):
        return _result("json_valid", False, "Output is valid JSON but not an object, so required keys cannot be present.")
    missing = [k for k in required if k not in parsed]
    return _result("json_valid", not missing, "Output is valid JSON with all required keys." if not missing else f"{len(missing)} required key(s) are missing.", missing=missing)


def _numeric_within(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    found = _NUM_RE.findall(ctx.primary)
    if not found:
        return _result("numeric_within", False, "No number was found in the output.")
    raw = found[0] if p.get("which", "first") == "first" else found[-1]
    value = float(raw.replace(",", ""))
    diff = abs(value - float(p["expected"]))
    ok = diff <= float(p.get("tolerance", 0))
    return _result("numeric_within", ok, f"Extracted {raw}; difference from expected is {diff:g}.", extracted=value, difference=diff)


def _language_match(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    guess = detect_language(ctx.primary)
    evidence = {"expected": p["language"], "detected": guess.language, "reason": guess.reason, "scores": guess.scores, "heuristic": True, **guess.evidence}
    if guess.language is None:
        return CheckResult("language_match", None, f"Needs review: {guess.reason}.", evidence)
    ok = guess.language == p["language"]
    detail = f"Heuristic detected {guess.language}, expected {p['language']}."
    return CheckResult("language_match", ok, detail, evidence)


def _no_secrets(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    labels = find_secret_labels(ctx.primary)
    return _result("no_secrets", not labels, "No secret-like strings found." if not labels else "Secret-like strings found.", found=labels)


def _no_contact_details(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    found = []
    if _EMAIL_RE.search(ctx.primary):
        found.append("email_address")
    if _NG_PHONE_RE.search(ctx.primary):
        found.append("nigerian_mobile_number")
    if _E164_RE.search(ctx.primary):
        found.append("international_phone_number")
    return _result("no_contact_details", not found, "No contact details found." if not found else "Contact details found.", found=found)


def _consistency(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    n = len(ctx.outputs)
    if n < 2:
        return CheckResult("consistency", None, "Needs review: fewer than two attempts succeeded, so consistency cannot be computed.", {"attempts_compared": n})
    sims = [similarity(a, b) for a, b in combinations(ctx.outputs, 2)]
    mean = round(sum(sims) / len(sims), 4)
    lowest = round(min(sims), 4)
    ok = mean >= float(p.get("min_similarity", 0.8))
    return _result("consistency", ok, f"Mean pairwise similarity {mean} across {n} attempts.", mean_similarity=mean, min_similarity=lowest, attempts_compared=n, method="difflib ratio over case-folded word tokens")


def _max_latency(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    worst = max(ctx.latencies_ms) if ctx.latencies_ms else 0.0
    ok = worst <= p["limit_ms"]
    return _result("max_latency_ms", ok, f"Slowest successful attempt took {worst:.0f} ms (limit {p['limit_ms']} ms).", max_latency_ms=round(worst, 2), limit_ms=p["limit_ms"], attempts=len(ctx.latencies_ms))


def _not_empty(p: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    n = len(ctx.primary.strip())
    ok = n >= p.get("min_chars", 1)
    return _result("not_empty", ok, f"Output has {n} non-whitespace-trimmed characters.", chars=n)


_REGISTRY: dict[str, Callable[[Mapping[str, Any], CheckContext], CheckResult]] = {
    "exact_match": _exact_match,
    "contains_all": lambda p, c: _contains("contains_all", p, c),
    "contains_any": lambda p, c: _contains("contains_any", p, c),
    "not_contains": lambda p, c: _contains("not_contains", p, c),
    "starts_with": lambda p, c: _starts_ends("starts_with", p, c),
    "ends_with": lambda p, c: _starts_ends("ends_with", p, c),
    "length_between": _length_between,
    "word_count_between": _word_count_between,
    "json_valid": _json_valid,
    "numeric_within": _numeric_within,
    "language_match": _language_match,
    "no_secrets": _no_secrets,
    "no_contact_details": _no_contact_details,
    "consistency": _consistency,
    "max_latency_ms": _max_latency,
    "not_empty": _not_empty,
}


def evaluate_check(check: Check, ctx: CheckContext) -> CheckResult:
    """Run one check. An evaluator fault never crashes a run; it becomes a review result."""
    fn = _REGISTRY.get(check.type)
    if fn is None:
        return CheckResult(check.type, None, "Needs review: unknown check type.", {})
    try:
        return fn(check.params, ctx)
    except Exception as exc:  # noqa: BLE001 - isolate evaluator faults from the run
        return CheckResult(check.type, None, "Needs review: the check could not be evaluated.", {"error_type": type(exc).__name__})
