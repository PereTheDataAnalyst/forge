"""A machine-readable description of what an evaluation dataset may contain.

Built from the validator's own specifications (``CHECK_SPECS``), so the description cannot drift
from what is actually accepted. It serves the MCP ``evaluate`` tool and any form builder in the
Evaluation Lab. The JSON Schema is never stricter than ``validate_dataset``: a dataset the
validator accepts always satisfies the schema. Rules a schema cannot express (a consistency check
needs ``repeat`` of 2 or more, case ids must be unique, the attempt budget) stay in the validator.
"""

from __future__ import annotations

from typing import Any

from ..natlas_facts import SUPPORTED_LANGUAGE_CODES
from .models import CATEGORIES
from .validation import CHECK_SPECS, EvaluationLimits, _Bool, _Enum, _Int, _Num, _Spec, _Str, _StrList

CHECK_DESCRIPTIONS: dict[str, str] = {
    "exact_match": "The output equals `expected` after normalisation.",
    "contains_all": "The output contains every string in `values`.",
    "contains_any": "The output contains at least one string in `values`.",
    "not_contains": "The output contains none of the strings in `values`.",
    "starts_with": "The output starts with `value`.",
    "ends_with": "The output ends with `value`.",
    "length_between": "The trimmed output length in characters is within `min_chars` and `max_chars` (give at least one).",
    "word_count_between": "The word count is within `min_words` and `max_words` (give at least one).",
    "json_valid": "The output parses as JSON and has every key in `required_keys`. A json code fence is accepted unless `allow_code_fence` is false.",
    "numeric_within": "The first (or last) number in the output is within `tolerance` of `expected`.",
    "language_match": "A heuristic language check agrees with `language`. It reports review when it cannot decide.",
    "no_secrets": "The output contains no secret-like strings such as bearer tokens or API keys.",
    "no_contact_details": "The output contains no email addresses or phone numbers.",
    "consistency": "Repeated attempts of one case give similar outputs. The case needs `repeat` of 2 or more.",
    "max_latency_ms": "The slowest successful attempt finished within `limit_ms`.",
    "not_empty": "The output has at least `min_chars` characters after trimming.",
}

_ID_PATTERN = "^[A-Za-z0-9_.:-]{1,64}$"
_TAG_PATTERN = "^[A-Za-z0-9_.:-]{1,40}$"


def _spec_schema(spec: _Spec) -> dict[str, Any]:
    if isinstance(spec, _Str):
        return {"type": "string", "minLength": spec.min_len, "maxLength": spec.max_len}
    if isinstance(spec, _Bool):
        return {"type": "boolean"}
    if isinstance(spec, _Int):
        return {"type": "integer", "minimum": spec.lo, "maximum": spec.hi}
    if isinstance(spec, _Num):
        return {"type": "number", "minimum": spec.lo, "maximum": spec.hi}
    if isinstance(spec, _Enum):
        return {"type": "string", "enum": list(spec.choices)}
    if isinstance(spec, _StrList):
        return {
            "type": "array",
            "minItems": spec.min_items,
            "maxItems": spec.max_items,
            "items": {"type": "string", "minLength": spec.min_len, "maxLength": spec.max_len},
        }
    raise TypeError(f"unsupported spec type {type(spec).__name__}")  # pragma: no cover - guards future spec kinds


def check_catalogue() -> list[dict[str, Any]]:
    """Every check type with its description and parameters."""
    catalogue: list[dict[str, Any]] = []
    for check_type, params in CHECK_SPECS.items():
        described: dict[str, Any] = {}
        for name, spec in params.items():
            entry = _spec_schema(spec)
            entry["required"] = bool(spec.required)
            if spec.default is not None:
                entry["default"] = list(spec.default) if isinstance(spec.default, list) else spec.default
            described[name] = entry
        catalogue.append({"type": check_type, "description": CHECK_DESCRIPTIONS[check_type], "params": described})
    return catalogue


def _check_schema(check_type: str) -> dict[str, Any]:
    params = CHECK_SPECS[check_type]
    required = ["type"] + [name for name, spec in params.items() if spec.required]
    properties: dict[str, Any] = {"type": {"const": check_type}}
    for name, spec in params.items():
        properties[name] = _spec_schema(spec)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def dataset_json_schema(limits: EvaluationLimits | None = None) -> dict[str, Any]:
    """JSON Schema (2020-12) for an evaluation dataset under the given limits."""
    lim = limits or EvaluationLimits()
    languages = list(SUPPORTED_LANGUAGE_CODES)
    case = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "prompt", "checks"],
        "properties": {
            "id": {"type": "string", "pattern": _ID_PATTERN},
            "name": {"type": "string", "minLength": 1, "maxLength": lim.max_name_chars},
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "prompt": {"type": "string", "minLength": 1, "maxLength": lim.max_prompt_chars},
            "system": {"type": "string", "minLength": 1, "maxLength": lim.max_system_chars},
            "language": {"type": "string", "enum": languages},
            "repeat": {"type": "integer", "minimum": 1, "maximum": lim.max_repeat},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": lim.max_max_tokens},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "tags": {
                "type": "array",
                "maxItems": lim.max_tags,
                "items": {"type": "string", "pattern": _TAG_PATTERN},
            },
            "checks": {
                "type": "array",
                "minItems": 1,
                "maxItems": lim.max_checks_per_case,
                "items": {"oneOf": [_check_schema(t) for t in CHECK_SPECS]},
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "cases"],
        "properties": {
            "id": {"type": "string", "pattern": _ID_PATTERN},
            "name": {"type": "string", "minLength": 1, "maxLength": lim.max_name_chars},
            "description": {"type": "string", "maxLength": lim.max_description_chars},
            "version": {"type": "string", "minLength": 1, "maxLength": 64},
            "cases": {"type": "array", "minItems": 1, "maxItems": lim.max_cases, "items": case},
        },
    }
