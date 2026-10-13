"""MCP token scopes.

A token carries an explicit set of scopes and every tool declares the one scope it needs.
There is no wildcard and no "admin" scope: nothing in this server can administer a project.
New tokens default to the read-only scopes.
"""

from __future__ import annotations

from typing import Iterable

from ..errors import ValidationFailed, ValidationIssue

SCOPE_MODELS_READ = "models:read"
SCOPE_PROJECT_READ = "project:read"
SCOPE_GENERATE = "generate"
SCOPE_TRANSCRIBE = "transcribe"
SCOPE_EVALUATE = "evaluate"
SCOPE_USAGE_READ = "usage:read"

SCOPE_DESCRIPTIONS: dict[str, str] = {
    SCOPE_MODELS_READ: "Read verified N-ATLAS model information.",
    SCOPE_PROJECT_READ: "Read this token's own project information and limits.",
    SCOPE_GENERATE: "Call N-ATLAS text generation (generate and chat).",
    SCOPE_TRANSCRIBE: "Call N-ATLAS speech transcription.",
    SCOPE_EVALUATE: "Run small developer-defined evaluations.",
    SCOPE_USAGE_READ: "Read aggregate usage for this token's project.",
}

ALL_SCOPES: frozenset[str] = frozenset(SCOPE_DESCRIPTIONS)
DEFAULT_SCOPES: frozenset[str] = frozenset({SCOPE_MODELS_READ, SCOPE_PROJECT_READ})


def normalize_scopes(values: Iterable[object], *, path: str = "scopes", allow_empty: bool = False) -> frozenset[str]:
    """Validate requested scopes. Unknown scopes are rejected, never ignored."""
    issues: list[ValidationIssue] = []
    cleaned: set[str] = set()
    items = list(values)
    if len(items) > len(ALL_SCOPES) * 2:
        raise ValidationFailed([ValidationIssue(path, "too many scopes")])
    for index, item in enumerate(items):
        if not isinstance(item, str) or item.strip() not in ALL_SCOPES:
            issues.append(ValidationIssue(f"{path}[{index}]", "must be one of: " + ", ".join(sorted(ALL_SCOPES))))
        else:
            cleaned.add(item.strip())
    if not cleaned and not issues and not allow_empty:
        issues.append(ValidationIssue(path, "at least one scope is required"))
    if issues:
        raise ValidationFailed(issues)
    return frozenset(cleaned)
