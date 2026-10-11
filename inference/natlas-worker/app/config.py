"""Worker settings, read from NATLAS_WORKER_* environment variables and an optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NATLAS_WORKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    token: SecretStr
    model_id: str = "NCAIR1/N-ATLaS"
    dtype: Literal["auto", "float16", "bfloat16", "float32"] = "auto"
    device_map: str = "auto"
    context_limit_tokens: int = Field(default=8092, ge=256, le=131072)
    max_new_tokens_limit: int = Field(default=4096, ge=1, le=8192)
    max_messages: int = Field(default=100, ge=1, le=200)
    max_prompt_chars: int = Field(default=200_000, ge=100, le=1_000_000)
    max_body_bytes: int = Field(default=1_048_576, ge=1024, le=20_000_000)
    max_concurrency: int = Field(default=1, ge=1, le=8)
    max_queue: int = Field(default=4, ge=0, le=64)
    max_generation_seconds: float = Field(default=120.0, gt=0, le=900)
    load_on_startup: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("token")
    @classmethod
    def _token_is_strong(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw.startswith("CHANGE_ME") or len(raw) < 32:
            raise ValueError(
                "NATLAS_WORKER_TOKEN must be a real secret of at least 32 characters. "
                "Run scripts/setup-local.ps1 to generate one."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> WorkerSettings:
    return WorkerSettings()  # type: ignore[call-arg]
