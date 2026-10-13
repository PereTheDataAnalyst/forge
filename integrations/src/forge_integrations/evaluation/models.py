"""Evaluation data model. Plain dataclasses so any persistence layer can map them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORIES: tuple[str, ...] = ("factual", "language", "consistency", "safety", "format", "general")

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_REVIEW = "review"  # a heuristic check could not decide
STATUS_ERROR = "error"  # the model call failed, so nothing could be evaluated
STATUS_SKIPPED = "skipped"
STATUSES: tuple[str, ...] = (STATUS_PASS, STATUS_FAIL, STATUS_REVIEW, STATUS_ERROR, STATUS_SKIPPED)

MAX_STORED_OUTPUT_CHARS = 50_000


@dataclass(frozen=True)
class Check:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.params}


@dataclass(frozen=True)
class Case:
    id: str
    name: str
    prompt: str
    checks: tuple[Check, ...]
    category: str = "general"
    system: str | None = None
    language: str | None = None
    repeat: int = 1
    max_tokens: int | None = None
    temperature: float | None = None
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "prompt": self.prompt,
            "system": self.system,
            "language": self.language,
            "repeat": self.repeat,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "checks": [c.to_dict() for c in self.checks],
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    description: str
    version: str
    cases: tuple[Case, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "cases": [c.to_dict() for c in self.cases],
        }


@dataclass(frozen=True)
class RunConfig:
    model: str | None = None
    prompt_version: str | None = None
    max_concurrency: int = 2
    call_timeout_seconds: float = 90.0
    default_max_tokens: int | None = None
    default_temperature: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "max_concurrency": self.max_concurrency,
            "call_timeout_seconds": self.call_timeout_seconds,
            "default_max_tokens": self.default_max_tokens,
            "default_temperature": self.default_temperature,
        }


@dataclass
class Attempt:
    index: int
    request_id: str
    ok: bool
    latency_ms: float  # end to end, measured by the runner around the gateway call
    output: str | None = None
    output_truncated: bool = False
    provider_latency_ms: float | None = None  # as reported by the gateway, when available
    model: str | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "request_id": self.request_id,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "provider_latency_ms": None if self.provider_latency_ms is None else round(self.provider_latency_ms, 2),
            "output": self.output,
            "output_truncated": self.output_truncated,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class CheckResult:
    type: str
    passed: bool | None  # None means the check could not decide and a human should look
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "passed": self.passed, "detail": self.detail, "evidence": self.evidence}


@dataclass
class CaseResult:
    case_id: str
    name: str
    category: str
    language: str | None
    status: str
    attempts: list[Attempt] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    partial_error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "category": self.category,
            "language": self.language,
            "status": self.status,
            "attempts": [a.to_dict() for a in self.attempts],
            "checks": [c.to_dict() for c in self.checks],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "partial_error_count": self.partial_error_count,
        }


@dataclass
class Run:
    run_id: str
    dataset_id: str
    dataset_name: str
    dataset_version: str
    model: str | None
    prompt_version: str | None
    project_id: str | None
    status: str  # "completed" | "cancelled"
    started_at: str
    finished_at: str
    config: dict[str, Any]
    results: list[CaseResult]
    summary: dict[str, Any]
    methodology: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "project_id": self.project_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "config": self.config,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "methodology": list(self.methodology),
        }
