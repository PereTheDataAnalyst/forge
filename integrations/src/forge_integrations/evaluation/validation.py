"""Strict, bounded validation of evaluation datasets.

All problems are collected and reported together with JSON-style paths such as
``cases[2].checks[0].values`` so a developer can fix a dataset in one pass.
Unknown keys are rejected rather than silently ignored.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..errors import ValidationFailed, ValidationIssue
from ..natlas_facts import SUPPORTED_LANGUAGE_CODES
from .models import CATEGORIES, Case, Check, Dataset

_INVALID = object()
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")


@dataclass(frozen=True)
class EvaluationLimits:
    max_cases: int = 200
    max_prompt_chars: int = 8000
    max_system_chars: int = 4000
    max_checks_per_case: int = 12
    max_repeat: int = 5
    max_tags: int = 10
    max_name_chars: int = 120
    max_description_chars: int = 2000
    max_max_tokens: int = 1024
    max_total_attempts: int = 600


class _Spec:
    def __init__(self, *, required: bool = False, default: Any = None) -> None:
        self.required = required
        self.default = default

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        raise NotImplementedError


class _Str(_Spec):
    def __init__(self, *, min_len: int = 0, max_len: int = 2000, **kw: Any) -> None:
        super().__init__(**kw)
        self.min_len, self.max_len = min_len, max_len

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if not isinstance(value, str):
            issues.append(ValidationIssue(path, "must be a string"))
            return _INVALID
        if _CTRL.search(value):
            issues.append(ValidationIssue(path, "must not contain control characters"))
            return _INVALID
        if len(value) < self.min_len or len(value) > self.max_len:
            issues.append(ValidationIssue(path, f"length must be between {self.min_len} and {self.max_len}"))
            return _INVALID
        if self.min_len > 0 and not value.strip():
            issues.append(ValidationIssue(path, "must not be blank"))
            return _INVALID
        return value


class _Bool(_Spec):
    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if not isinstance(value, bool):
            issues.append(ValidationIssue(path, "must be true or false"))
            return _INVALID
        return value


class _Int(_Spec):
    def __init__(self, *, lo: int, hi: int, **kw: Any) -> None:
        super().__init__(**kw)
        self.lo, self.hi = lo, hi

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            issues.append(ValidationIssue(path, "must be an integer"))
            return _INVALID
        if not self.lo <= value <= self.hi:
            issues.append(ValidationIssue(path, f"must be between {self.lo} and {self.hi}"))
            return _INVALID
        return value


class _Num(_Spec):
    def __init__(self, *, lo: float, hi: float, **kw: Any) -> None:
        super().__init__(**kw)
        self.lo, self.hi = lo, hi

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            issues.append(ValidationIssue(path, "must be a finite number"))
            return _INVALID
        if not self.lo <= value <= self.hi:
            issues.append(ValidationIssue(path, f"must be between {self.lo} and {self.hi}"))
            return _INVALID
        return float(value) if isinstance(value, float) else value


class _Enum(_Spec):
    def __init__(self, choices: tuple[str, ...], **kw: Any) -> None:
        super().__init__(**kw)
        self.choices = choices

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if not isinstance(value, str) or value not in self.choices:
            issues.append(ValidationIssue(path, f"must be one of: {', '.join(self.choices)}"))
            return _INVALID
        return value


class _StrList(_Spec):
    def __init__(self, *, max_items: int, min_len: int = 1, max_len: int = 500, min_items: int = 0, **kw: Any) -> None:
        super().__init__(**kw)
        self.max_items, self.min_len, self.max_len, self.min_items = max_items, min_len, max_len, min_items

    def check(self, value: Any, path: str, issues: list[ValidationIssue]) -> Any:
        if not isinstance(value, list):
            issues.append(ValidationIssue(path, "must be a list of strings"))
            return _INVALID
        if len(value) < self.min_items or len(value) > self.max_items:
            issues.append(ValidationIssue(path, f"must contain between {self.min_items} and {self.max_items} items"))
            return _INVALID
        cleaned: list[str] = []
        ok = True
        item_spec = _Str(min_len=self.min_len, max_len=self.max_len)
        for i, item in enumerate(value):
            result = item_spec.check(item, f"{path}[{i}]", issues)
            if result is _INVALID:
                ok = False
            else:
                cleaned.append(result)
        return cleaned if ok else _INVALID


_LANG = tuple(SUPPORTED_LANGUAGE_CODES)

_NORM: dict[str, _Spec] = {
    "ignore_case": _Bool(default=True),
    "ignore_diacritics": _Bool(default=False),
    "normalize_whitespace": _Bool(default=True),
    "strip_punctuation": _Bool(default=False),
}

CHECK_SPECS: dict[str, dict[str, _Spec]] = {
    "exact_match": {"expected": _Str(min_len=1, max_len=2000, required=True), **_NORM},
    "contains_all": {"values": _StrList(max_items=20, min_items=1, required=True), **_NORM},
    "contains_any": {"values": _StrList(max_items=20, min_items=1, required=True), **_NORM},
    "not_contains": {"values": _StrList(max_items=50, min_items=1, required=True), **_NORM},
    "starts_with": {"value": _Str(min_len=1, max_len=500, required=True), **_NORM},
    "ends_with": {"value": _Str(min_len=1, max_len=500, required=True), **_NORM},
    "length_between": {"min_chars": _Int(lo=0, hi=100_000), "max_chars": _Int(lo=0, hi=100_000)},
    "word_count_between": {"min_words": _Int(lo=0, hi=20_000), "max_words": _Int(lo=0, hi=20_000)},
    "json_valid": {"allow_code_fence": _Bool(default=True), "required_keys": _StrList(max_items=20, max_len=100, default=[])},
    "numeric_within": {
        "expected": _Num(lo=-1e15, hi=1e15, required=True),
        "tolerance": _Num(lo=0, hi=1e15, default=0),
        "which": _Enum(("first", "last"), default="first"),
    },
    "language_match": {"language": _Enum(_LANG, required=True)},
    "no_secrets": {},
    "no_contact_details": {},
    "consistency": {"min_similarity": _Num(lo=0, hi=1, default=0.8)},
    "max_latency_ms": {"limit_ms": _Int(lo=1, hi=600_000, required=True)},
    "not_empty": {"min_chars": _Int(lo=1, hi=10_000, default=1)},
}

CHECK_TYPES: tuple[str, ...] = tuple(CHECK_SPECS)


def _at_least_one(a: str, b: str) -> Callable[[dict[str, Any], str, list[ValidationIssue]], None]:
    def post(params: dict[str, Any], path: str, issues: list[ValidationIssue]) -> None:
        if params.get(a) is None and params.get(b) is None:
            issues.append(ValidationIssue(path, f"provide {a}, {b}, or both"))
        elif params.get(a) is not None and params.get(b) is not None and params[a] > params[b]:
            issues.append(ValidationIssue(path, f"{a} must not exceed {b}"))

    return post


_POST: dict[str, Callable[[dict[str, Any], str, list[ValidationIssue]], None]] = {
    "length_between": _at_least_one("min_chars", "max_chars"),
    "word_count_between": _at_least_one("min_words", "max_words"),
}


def _validate_check(raw: Any, path: str, issues: list[ValidationIssue], repeat: int) -> Check | None:
    if not isinstance(raw, Mapping):
        issues.append(ValidationIssue(path, "must be an object"))
        return None
    ctype = raw.get("type")
    if not isinstance(ctype, str) or ctype not in CHECK_SPECS:
        issues.append(ValidationIssue(f"{path}.type", f"must be one of: {', '.join(CHECK_TYPES)}"))
        return None
    specs = CHECK_SPECS[ctype]
    before = len(issues)
    for key in raw:
        if key != "type" and key not in specs:
            issues.append(ValidationIssue(f"{path}.{key}", "unknown parameter for this check type"))
    params: dict[str, Any] = {}
    for name, spec in specs.items():
        if name in raw and raw[name] is not None:
            value = spec.check(raw[name], f"{path}.{name}", issues)
            if value is not _INVALID:
                params[name] = value
        elif spec.required:
            issues.append(ValidationIssue(f"{path}.{name}", "is required"))
        elif spec.default is not None:
            params[name] = list(spec.default) if isinstance(spec.default, list) else spec.default
    if len(issues) == before and ctype in _POST:
        _POST[ctype](params, path, issues)
    if ctype == "consistency" and repeat < 2:
        issues.append(ValidationIssue(path, "a consistency check needs the case to set repeat to 2 or more"))
    return Check(type=ctype, params=params) if len(issues) == before else None


_CASE_KEYS = {"id", "name", "category", "prompt", "system", "language", "repeat", "max_tokens", "temperature", "checks", "tags"}
_DATASET_KEYS = {"id", "name", "description", "version", "cases"}


def _validate_case(raw: Any, index: int, limits: EvaluationLimits, issues: list[ValidationIssue]) -> Case | None:
    path = f"cases[{index}]"
    if not isinstance(raw, Mapping):
        issues.append(ValidationIssue(path, "must be an object"))
        return None
    before = len(issues)
    for key in raw:
        if key not in _CASE_KEYS:
            issues.append(ValidationIssue(f"{path}.{key}", "unknown field"))

    def field(name: str, spec: _Spec) -> Any:
        if name in raw and raw[name] is not None:
            value = spec.check(raw[name], f"{path}.{name}", issues)
            return None if value is _INVALID else value
        if spec.required:
            issues.append(ValidationIssue(f"{path}.{name}", "is required"))
        return spec.default

    case_id = field("id", _Str(min_len=1, max_len=64, default=f"c{index + 1:03d}"))
    if isinstance(case_id, str) and not _ID_RE.match(case_id):
        issues.append(ValidationIssue(f"{path}.id", "may only contain letters, digits, and _ . : -"))
    name = field("name", _Str(min_len=1, max_len=limits.max_name_chars, required=True))
    category = field("category", _Enum(CATEGORIES, default="general"))
    prompt = field("prompt", _Str(min_len=1, max_len=limits.max_prompt_chars, required=True))
    system = field("system", _Str(min_len=1, max_len=limits.max_system_chars))
    language = field("language", _Enum(_LANG))
    repeat = field("repeat", _Int(lo=1, hi=limits.max_repeat, default=1))
    max_tokens = field("max_tokens", _Int(lo=1, hi=limits.max_max_tokens))
    temperature = field("temperature", _Num(lo=0, hi=2))
    tags_raw = field("tags", _StrList(max_items=limits.max_tags, max_len=40, default=[]))
    tags: list[str] = tags_raw if isinstance(tags_raw, list) else []
    for t_i, tag in enumerate(tags):
        if not _TAG_RE.match(tag):
            issues.append(ValidationIssue(f"{path}.tags[{t_i}]", "may only contain letters, digits, and _ . : -"))

    checks: list[Check] = []
    raw_checks = raw.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        issues.append(ValidationIssue(f"{path}.checks", "must be a non-empty list"))
    elif len(raw_checks) > limits.max_checks_per_case:
        issues.append(ValidationIssue(f"{path}.checks", f"at most {limits.max_checks_per_case} checks per case"))
    else:
        repeat_value = repeat if isinstance(repeat, int) else 1
        for c_i, raw_check in enumerate(raw_checks):
            check = _validate_check(raw_check, f"{path}.checks[{c_i}]", issues, repeat_value)
            if check is not None:
                checks.append(check)

    if len(issues) != before:
        return None
    return Case(
        id=case_id,
        name=name.strip(),
        prompt=prompt,
        checks=tuple(checks),
        category=category,
        system=system,
        language=language,
        repeat=repeat,
        max_tokens=max_tokens,
        temperature=temperature,
        tags=tuple(tags),
    )


def validate_dataset(
    raw: Any,
    *,
    limits: EvaluationLimits | None = None,
    new_dataset_id: Callable[[], str] | None = None,
) -> Dataset:
    """Validate a dataset document and return an immutable ``Dataset``.

    Raises ``ValidationFailed`` with every problem found.
    """
    lim = limits or EvaluationLimits()
    issues: list[ValidationIssue] = []
    if not isinstance(raw, Mapping):
        raise ValidationFailed([ValidationIssue("", "dataset must be an object")])
    for key in raw:
        if key not in _DATASET_KEYS:
            issues.append(ValidationIssue(key, "unknown field"))

    def top(name: str, spec: _Spec) -> Any:
        if name in raw and raw[name] is not None:
            value = spec.check(raw[name], name, issues)
            return None if value is _INVALID else value
        if spec.required:
            issues.append(ValidationIssue(name, "is required"))
        return spec.default

    from uuid import uuid4

    dataset_id = top("id", _Str(min_len=1, max_len=64, default=None))
    if dataset_id is None:
        dataset_id = new_dataset_id() if new_dataset_id else str(uuid4())
    elif not _ID_RE.match(dataset_id):
        issues.append(ValidationIssue("id", "may only contain letters, digits, and _ . : -"))
    name = top("name", _Str(min_len=1, max_len=lim.max_name_chars, required=True))
    description = top("description", _Str(max_len=lim.max_description_chars, default=""))
    version = top("version", _Str(min_len=1, max_len=64, default="1"))

    raw_cases = raw.get("cases")
    cases: list[Case] = []
    if not isinstance(raw_cases, list) or not raw_cases:
        issues.append(ValidationIssue("cases", "must be a non-empty list"))
    elif len(raw_cases) > lim.max_cases:
        issues.append(ValidationIssue("cases", f"at most {lim.max_cases} cases per dataset"))
    else:
        for i, raw_case in enumerate(raw_cases):
            case = _validate_case(raw_case, i, lim, issues)
            if case is not None:
                cases.append(case)
        seen: set[str] = set()
        for i, case in enumerate(cases):
            if case.id in seen:
                issues.append(ValidationIssue(f"cases[{i}].id", "duplicate case id"))
            seen.add(case.id)
        if sum(c.repeat for c in cases) > lim.max_total_attempts:
            issues.append(ValidationIssue("cases", f"total attempts (sum of repeat) must not exceed {lim.max_total_attempts}"))

    if issues:
        raise ValidationFailed(issues)
    return Dataset(
        id=dataset_id,
        name=name.strip(),
        description=description or "",
        version=version,
        cases=tuple(cases),
    )
