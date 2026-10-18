"""Request bodies for the evaluation endpoints."""

import uuid
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCreateIn(BaseModel):
    """Create a dataset from a starter or from a document the developer wrote."""

    model_config = ConfigDict(extra="forbid")

    starter_id: str | None = Field(default=None, min_length=1, max_length=64)
    dataset: dict[str, Any] | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.starter_id is None) == (self.dataset is None):
            raise ValueError("Send either starter_id or dataset, not both and not neither.")
        return self


class RunStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: uuid.UUID
    model: str | None = Field(default=None, min_length=1, max_length=120)
    # A label such as "v3" to compare runs. Printable characters only: it ends up in exports.
    prompt_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 _.:/@+#-]{1,64}$")
