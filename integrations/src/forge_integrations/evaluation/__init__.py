"""Evaluation Lab engine.

Deterministic, explainable evaluation of N-ATLAS outputs. Every score is derived
from a check the developer defined; there is no accuracy score and no model-based
judge. See ``docs/evaluation.md`` for the methodology and its limits.
"""

from .metrics import METHODOLOGY, compare_runs, summarize
from .models import (
    CATEGORIES,
    Attempt,
    Case,
    CaseResult,
    Check,
    CheckResult,
    Dataset,
    Run,
    RunConfig,
)
from .runner import EvaluationRunner
from .service import InMemoryRunStore, RunHandle, RunManager, RunStore, run_summary
from .starter import describe_starter_datasets, load_starter_dataset, load_starter_document, starter_dataset_ids
from .validation import EvaluationLimits, validate_dataset

__all__ = [
    "CATEGORIES",
    "METHODOLOGY",
    "Attempt",
    "Case",
    "CaseResult",
    "Check",
    "CheckResult",
    "Dataset",
    "EvaluationLimits",
    "EvaluationRunner",
    "InMemoryRunStore",
    "Run",
    "RunConfig",
    "RunHandle",
    "RunManager",
    "RunStore",
    "compare_runs",
    "describe_starter_datasets",
    "load_starter_dataset",
    "load_starter_document",
    "run_summary",
    "starter_dataset_ids",
    "summarize",
    "validate_dataset",
]
