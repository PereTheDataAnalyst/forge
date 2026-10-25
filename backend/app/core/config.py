"""Application settings loaded from environment variables and an optional .env file."""

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.net import normalize_origin

REPO_ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER_PREFIX = "CHANGE_ME"


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("FORGE_ENV_FILE") or str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    # Runtime
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "N-ATLAS Forge"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    enable_api_docs: bool = False

    # Data stores
    database_url: str
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=120)
    db_statement_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    redis_url: str
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Secrets
    secret_key: SecretStr
    api_key_pepper: SecretStr

    # Sessions and cookies
    cookie_secure: bool = False
    session_cookie_name: str = "forge_session"
    csrf_cookie_name: str = "forge_csrf"
    session_idle_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    session_absolute_ttl_hours: int = Field(default=24 * 14, ge=1, le=24 * 90)

    # Access
    registration_enabled: bool = True

    # Network edge
    public_base_url: str = "http://localhost:5173"
    cors_allowed_origins: str = ""
    trusted_proxy_count: int = Field(default=0, ge=0, le=5)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=50_000_000)
    # Directory that holds the built web app (frontend/dist). When set, this process serves the
    # web app next to the API from one origin. The production image sets it. Leave it unset in
    # development, where the Vite server serves the web app.
    frontend_dist_dir: str | None = None

    # Rate limiting
    auth_rate_limit_per_minute: int = Field(default=20, ge=1)
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    gateway_rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_fail_open: bool = False

    # N-ATLAS provider (the self-hosted inference worker that wraps the official model)
    natlas_model_id: str = "NCAIR1/N-ATLaS"
    natlas_inference_url: str | None = None
    natlas_inference_token: SecretStr | None = None
    natlas_allow_plaintext_remote: bool = False
    natlas_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    natlas_request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    natlas_health_cache_seconds: float = Field(default=10.0, ge=0, le=300)
    natlas_default_max_tokens: int = Field(default=512, ge=1, le=8192)
    natlas_max_tokens_limit: int = Field(default=2048, ge=1, le=8192)
    natlas_max_messages: int = Field(default=50, ge=1, le=200)
    natlas_max_input_chars: int = Field(default=32000, ge=100, le=500000)
    # Defaults mirror the example in the official N-ATLaS model card.
    natlas_default_temperature: float = Field(default=0.1, ge=0, le=2)
    natlas_default_top_p: float = Field(default=1.0, gt=0, le=1)
    natlas_default_repetition_penalty: float = Field(default=1.12, ge=1, le=2)

    # Evaluation Lab (docs/evaluation-lab.md). Runs call the model through the gateway like any
    # other request, so the model host's capacity is the real limit. Keep concurrency low.
    eval_max_cases: int = Field(default=100, ge=1, le=1000)
    eval_max_concurrency: int = Field(default=2, ge=1, le=16)
    eval_call_timeout_seconds: float = Field(default=90.0, ge=1, le=600)
    eval_max_concurrent_runs_per_project: int = Field(default=1, ge=1, le=16)
    eval_runs_per_hour: int = Field(default=20, ge=1, le=1000)
    eval_max_run_seconds: int = Field(default=900, ge=10, le=7200)
    eval_max_evaluations_per_project: int = Field(default=50, ge=1, le=500)

    # Voice: speech recognition runs in a separate worker (integrations/asr-service). Leave the
    # URL and token unset until that worker runs. See docs/voice.md.
    asr_service_url: str | None = None
    asr_service_token: SecretStr | None = None
    # Extra host names the worker may use, comma separated. The host in ASR_SERVICE_URL is
    # always allowed.
    asr_allowed_hosts: str = ""
    asr_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    # Allow a worker on a private network address. Only for a network you control.
    asr_allow_private_network: bool = False
    # Allow an http:// worker URL on a non-loopback host in production. Private network only.
    asr_allow_plaintext_remote: bool = False
    voice_max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=65536, le=25 * 1024 * 1024)
    voice_max_seconds: int = Field(default=30, ge=1, le=30)
    voice_rate_limit_per_minute: int = Field(default=10, ge=1, le=600)
    voice_max_concurrent: int = Field(default=4, ge=1, le=32)

    # MCP server, served by this process at /mcp. See docs/mcp.md.
    mcp_enabled: bool = True
    # Extra Host header values the MCP endpoint accepts, comma separated. The host of
    # PUBLIC_BASE_URL and loopback names are always accepted.
    mcp_allowed_hosts: str = ""

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        value = value.strip()
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                value = "postgresql+asyncpg://" + value[len(prefix) :]
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL such as "
                "postgresql+asyncpg://user:password@localhost:5432/forge_dev"
            )
        return value

    @field_validator("natlas_inference_url", mode="before")
    @classmethod
    def _blank_url_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().rstrip("/")
            return value or None
        return value

    @field_validator("natlas_inference_token", mode="before")
    @classmethod
    def _blank_token_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("asr_service_url", mode="before")
    @classmethod
    def _blank_asr_url_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().rstrip("/")
            return value or None
        return value

    @field_validator("asr_service_token", mode="before")
    @classmethod
    def _blank_asr_token_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_configuration(self) -> "Settings":
        secrets = {
            "SECRET_KEY": self.secret_key.get_secret_value(),
            "API_KEY_PEPPER": self.api_key_pepper.get_secret_value(),
        }
        for name, value in secrets.items():
            if self.app_env != "test" and value.startswith(PLACEHOLDER_PREFIX):
                raise ValueError(
                    f"{name} still has the placeholder value. "
                    "Run scripts/setup-local.ps1 or set a real random secret."
                )
            if len(value) < 32:
                raise ValueError(f"{name} must be at least 32 characters long.")

        if self.natlas_default_max_tokens > self.natlas_max_tokens_limit:
            raise ValueError("NATLAS_DEFAULT_MAX_TOKENS must not exceed NATLAS_MAX_TOKENS_LIMIT.")

        for raw in self.cors_allowed_origins.split(","):
            if raw.strip() and normalize_origin(raw) is None:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must be a comma-separated list of origins such as "
                    "https://app.example.com. Wildcards are not allowed."
                )

        if (self.natlas_inference_url is None) != (self.natlas_inference_token is None):
            raise ValueError(
                "Set both NATLAS_INFERENCE_URL and NATLAS_INFERENCE_TOKEN, or neither."
            )
        if self.natlas_inference_url:
            parts = urlsplit(self.natlas_inference_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("NATLAS_INFERENCE_URL must be an http(s) URL with a host.")
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError(
                    "NATLAS_INFERENCE_URL must not contain credentials, a query or a fragment."
                )
            if (
                self.is_production
                and parts.scheme == "http"
                and not _is_loopback_host(parts.hostname)
                and not self.natlas_allow_plaintext_remote
            ):
                raise ValueError(
                    "In production NATLAS_INFERENCE_URL must use https unless the worker is on "
                    "loopback. Set NATLAS_ALLOW_PLAINTEXT_REMOTE=true only for a private network."
                )

        if (self.asr_service_url is None) != (self.asr_service_token is None):
            raise ValueError("Set both ASR_SERVICE_URL and ASR_SERVICE_TOKEN, or neither.")
        if self.asr_service_url:
            asr = urlsplit(self.asr_service_url)
            if asr.scheme not in ("http", "https") or not asr.hostname:
                raise ValueError("ASR_SERVICE_URL must be an http(s) URL with a host.")
            if asr.username or asr.password or asr.query or asr.fragment:
                raise ValueError(
                    "ASR_SERVICE_URL must not contain credentials, a query or a fragment."
                )
            if (
                self.is_production
                and asr.scheme == "http"
                and not _is_loopback_host(asr.hostname)
                and not self.asr_allow_plaintext_remote
            ):
                raise ValueError(
                    "In production ASR_SERVICE_URL must use https unless the worker is on "
                    "loopback. Set ASR_ALLOW_PLAINTEXT_REMOTE=true only for a private network."
                )

        if self.is_production:
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production.")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use https in production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        origins: list[str] = []
        for raw in self.cors_allowed_origins.split(","):
            normalized = normalize_origin(raw)
            if raw.strip() and normalized:
                origins.append(normalized)
        return origins

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Origins allowed to make state-changing browser requests."""
        origins = set(self.cors_origins_list)
        base = normalize_origin(self.public_base_url)
        if base:
            origins.add(base)
        if self.app_env == "development":
            origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
        return frozenset(origins)

    @property
    def natlas_configured(self) -> bool:
        return bool(self.natlas_inference_url and self.natlas_inference_token)

    @property
    def asr_configured(self) -> bool:
        return bool(self.asr_service_url and self.asr_service_token)

    def safe_summary(self) -> dict[str, object]:
        """Non-sensitive configuration summary for the startup log."""
        db = urlsplit(self.database_url)
        redis = urlsplit(self.redis_url)
        return {
            "environment": self.app_env,
            "database_host": db.hostname,
            "database_name": db.path.lstrip("/"),
            "redis_host": redis.hostname,
            "natlas_configured": self.natlas_configured,
            "natlas_model_id": self.natlas_model_id,
            "asr_configured": self.asr_configured,
            "mcp_enabled": self.mcp_enabled,
            "cookie_secure": self.cookie_secure,
            "api_docs_enabled": self.enable_api_docs,
            "web_app_served": bool((self.frontend_dist_dir or "").strip()),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
