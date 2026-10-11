import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, StringConstraints


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


Email = Annotated[EmailStr, BeforeValidator(_strip)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    # Passwords are never stripped or altered. Length rules are enforced by the password policy.
    password: str = Field(min_length=1, max_length=128)
    display_name: DisplayName | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime
