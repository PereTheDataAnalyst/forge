"""Starter evaluation datasets that ship with the package.

They are examples to copy and edit, not benchmarks. Each case lists a few accepted answers and
every check is deterministic, so a ``fail`` means "the output matched none of the listed forms"
and never "the model is wrong". See ``docs/evaluation.md``.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from ..errors import ErrorCode, ForgeError
from .models import Dataset
from .validation import EvaluationLimits, validate_dataset

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_FILE_BYTES = 256 * 1024


def _directory():
    return resources.files(__package__).joinpath("starter")


def starter_dataset_ids() -> list[str]:
    """Ids of the bundled datasets, sorted."""
    return sorted(entry.name[: -len(".json")] for entry in _directory().iterdir() if entry.name.endswith(".json"))


def load_starter_document(dataset_id: str) -> dict[str, Any]:
    """The raw JSON document, for showing or copying. Unknown ids are ``not_found``."""
    if not isinstance(dataset_id, str) or not _ID_RE.match(dataset_id) or dataset_id not in starter_dataset_ids():
        raise ForgeError(ErrorCode.NOT_FOUND, "No such starter dataset.", http_status=404)
    raw = _directory().joinpath(f"{dataset_id}.json").read_bytes()
    if len(raw) > _MAX_FILE_BYTES:
        raise ForgeError(ErrorCode.INTERNAL, "The starter dataset is unreadable.", http_status=500)
    return json.loads(raw.decode("utf-8"))


def load_starter_dataset(dataset_id: str, *, limits: EvaluationLimits | None = None) -> Dataset:
    """A validated, ready to run ``Dataset``."""
    return validate_dataset(load_starter_document(dataset_id), limits=limits)


def describe_starter_datasets() -> list[dict[str, Any]]:
    """What a picker needs: identity, size and what each dataset covers. No prompts."""
    rows = []
    for dataset_id in starter_dataset_ids():
        document = load_starter_document(dataset_id)
        cases = document["cases"]
        rows.append(
            {
                "id": document["id"],
                "name": document["name"],
                "version": document["version"],
                "description": document["description"],
                "case_count": len(cases),
                "languages": sorted({c["language"] for c in cases if c.get("language")}),
                "categories": sorted({c.get("category", "general") for c in cases}),
            }
        )
    return rows
