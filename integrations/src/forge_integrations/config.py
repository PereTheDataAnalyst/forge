"""Environment-driven settings for the advanced integrations.

No secret has a default. Secrets are read from the environment only and are never included
in ``repr`` output. Invalid values fail loudly at start-up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

from .errors import ConfigError
from .security.hostcheck import parse_host_rule, parse_origin_rule

MIB = 1024 * 1024


def _text(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _text(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer.") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}.")
    return value


def _float(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = _text(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number.") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}.")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = _text(env, name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false.")


def _csv(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = _text(env, name)
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class VoiceSettings:
    max_upload_bytes: int = 10 * MIB
    max_seconds: int = 30
    asr_service_url: str | None = None
    asr_service_token: str | None = field(default=None, repr=False)
    asr_allowed_hosts: tuple[str, ...] = ()
    asr_timeout_seconds: float = 60.0
    allow_private_targets: bool = False
    allow_insecure_http: bool = False


@dataclass(frozen=True)
class EvaluationSettings:
    max_cases: int = 200
    max_concurrency: int = 4
    call_timeout_seconds: float = 90.0
    max_concurrent_runs_per_project: int = 2


#: JSON-RPC envelope, argument names and metadata allowed on top of a base64 audio payload.
MCP_BODY_OVERHEAD_BYTES = 64 * 1024


def base64_length(raw_bytes: int) -> int:
    """Length of the padded base64 text for ``raw_bytes`` bytes."""
    return ((raw_bytes + 2) // 3) * 4


@dataclass(frozen=True)
class McpSettings:
    token_pepper: str | None = field(default=None, repr=False)
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = 8 * MIB
    max_audio_bytes: int = 5 * MIB
    rate_limit_default_per_minute: int = 60
    rate_limit_generate_per_minute: int = 20
    rate_limit_transcribe_per_minute: int = 10
    rate_limit_evaluate_per_minute: int = 3
    #: Aggregate ceiling across every token of one project, so many tokens cannot multiply a quota.
    rate_limit_project_per_minute: int = 300
    #: Requests of any kind (including discovery and listing) one token may make per minute.
    rate_limit_requests_per_minute: int = 240
    auth_failures_per_minute: int = 20
    generate_max_tokens: int = 1024
    evaluate_max_cases: int = 10
    evaluate_max_attempts: int = 30
    evaluate_max_seconds: int = 90
    evaluate_call_timeout_seconds: float = 45.0
    evaluate_max_concurrent: int = 2
    #: Upper bound for any tool other than ``evaluate`` (which derives its own bound).
    tool_timeout_seconds: float = 120.0
    #: Requests handled at once. Beyond this the server answers 503 instead of queueing without bound.
    max_concurrent_requests: int = 200
    #: How long a client may take to send a request body before it is cut off.
    body_read_timeout_seconds: float = 30.0
    token_default_ttl_days: int = 30
    token_max_ttl_days: int = 90

    def __post_init__(self) -> None:
        required_body = base64_length(self.max_audio_bytes) + MCP_BODY_OVERHEAD_BYTES
        if self.max_body_bytes < required_body:
            raise ConfigError(
                f"MCP_MAX_BODY_BYTES must be at least {required_body} so a base64 audio payload of "
                f"MCP_MAX_AUDIO_BYTES ({self.max_audio_bytes}) fits in one request."
            )
        if self.token_default_ttl_days > self.token_max_ttl_days:
            raise ConfigError("MCP_TOKEN_DEFAULT_TTL_DAYS must not exceed MCP_TOKEN_MAX_TTL_DAYS.")
        for name in (
            "rate_limit_default_per_minute",
            "rate_limit_generate_per_minute",
            "rate_limit_transcribe_per_minute",
            "rate_limit_evaluate_per_minute",
            "rate_limit_project_per_minute",
            "rate_limit_requests_per_minute",
            "auth_failures_per_minute",
            "generate_max_tokens",
            "evaluate_max_cases",
            "evaluate_max_attempts",
            "evaluate_max_seconds",
            "evaluate_max_concurrent",
        ):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be at least 1.")
        if self.tool_timeout_seconds < 1 or self.evaluate_call_timeout_seconds < 1 or self.body_read_timeout_seconds < 1:
            raise ConfigError("Timeouts must be at least 1 second.")
        if self.max_concurrent_requests < 1:
            raise ConfigError("max_concurrent_requests must be at least 1.")
        for entry in self.allowed_hosts:
            try:
                parse_host_rule(entry)
            except ValueError as exc:
                raise ConfigError(f"MCP_ALLOWED_HOSTS: {exc}") from None
        for entry in self.allowed_origins:
            try:
                parse_origin_rule(entry)
            except ValueError as exc:
                raise ConfigError(f"MCP_ALLOWED_ORIGINS: {exc}") from None

    @property
    def evaluate_tool_timeout_seconds(self) -> float:
        """Hard bound for one ``evaluate`` call: the run deadline plus the slowest in-flight model call."""
        return float(self.evaluate_max_seconds) + self.evaluate_call_timeout_seconds + 5.0


@dataclass(frozen=True)
class Settings:
    voice: VoiceSettings
    evaluation: EvaluationSettings
    mcp: McpSettings


def load_voice_settings(source: Mapping[str, str]) -> VoiceSettings:
    asr_url = _text(source, "ASR_SERVICE_URL")
    allowed_hosts = _csv(source, "ASR_ALLOWED_HOSTS")
    if asr_url and not allowed_hosts:
        host = (urlsplit(asr_url).hostname or "").lower()
        allowed_hosts = (host,) if host else ()
    return VoiceSettings(
        max_upload_bytes=_int(source, "VOICE_MAX_UPLOAD_BYTES", 10 * MIB, 1024, 100 * MIB),
        # The official ASR models accept at most 30 seconds per inference.
        max_seconds=_int(source, "VOICE_MAX_SECONDS", 30, 1, 30),
        asr_service_url=asr_url,
        asr_service_token=_text(source, "ASR_SERVICE_TOKEN"),
        asr_allowed_hosts=allowed_hosts,
        asr_timeout_seconds=_float(source, "ASR_TIMEOUT_SECONDS", 60.0, 1.0, 600.0),
        allow_private_targets=_bool(source, "ALLOW_PRIVATE_NETWORK_TARGETS", False),
        allow_insecure_http=_bool(source, "ALLOW_INSECURE_HTTP_TARGETS", False),
    )


def load_evaluation_settings(source: Mapping[str, str]) -> EvaluationSettings:
    return EvaluationSettings(
        max_cases=_int(source, "EVAL_MAX_CASES", 200, 1, 1000),
        max_concurrency=_int(source, "EVAL_MAX_CONCURRENCY", 4, 1, 16),
        call_timeout_seconds=_float(source, "EVAL_CALL_TIMEOUT_SECONDS", 90.0, 1.0, 600.0),
        max_concurrent_runs_per_project=_int(source, "EVAL_MAX_CONCURRENT_RUNS_PER_PROJECT", 2, 1, 16),
    )


def load_mcp_settings(source: Mapping[str, str]) -> McpSettings:
    default_ttl = _int(source, "MCP_TOKEN_DEFAULT_TTL_DAYS", 30, 1, 3650)
    max_ttl = _int(source, "MCP_TOKEN_MAX_TTL_DAYS", 90, 1, 3650)
    if default_ttl > max_ttl:
        raise ConfigError("MCP_TOKEN_DEFAULT_TTL_DAYS must not exceed MCP_TOKEN_MAX_TTL_DAYS.")
    return McpSettings(
        token_pepper=_text(source, "MCP_TOKEN_PEPPER"),
        allowed_hosts=_csv(source, "MCP_ALLOWED_HOSTS"),
        allowed_origins=_csv(source, "MCP_ALLOWED_ORIGINS"),
        max_body_bytes=_int(source, "MCP_MAX_BODY_BYTES", 8 * MIB, 4096, 64 * MIB),
        max_audio_bytes=_int(source, "MCP_MAX_AUDIO_BYTES", 5 * MIB, 1024, 25 * MIB),
        rate_limit_default_per_minute=_int(source, "MCP_RATE_LIMIT_PER_MINUTE", 60, 1, 10_000),
        rate_limit_generate_per_minute=_int(source, "MCP_RATE_LIMIT_GENERATE_PER_MINUTE", 20, 1, 10_000),
        rate_limit_transcribe_per_minute=_int(source, "MCP_RATE_LIMIT_TRANSCRIBE_PER_MINUTE", 10, 1, 10_000),
        rate_limit_evaluate_per_minute=_int(source, "MCP_RATE_LIMIT_EVALUATE_PER_MINUTE", 3, 1, 10_000),
        rate_limit_project_per_minute=_int(source, "MCP_RATE_LIMIT_PROJECT_PER_MINUTE", 300, 1, 100_000),
        rate_limit_requests_per_minute=_int(source, "MCP_RATE_LIMIT_REQUESTS_PER_MINUTE", 240, 1, 100_000),
        auth_failures_per_minute=_int(source, "MCP_AUTH_FAILURES_PER_MINUTE", 20, 1, 10_000),
        generate_max_tokens=_int(source, "MCP_GENERATE_MAX_TOKENS", 1024, 1, 8000),
        evaluate_max_cases=_int(source, "MCP_EVALUATE_MAX_CASES", 10, 1, 50),
        evaluate_max_attempts=_int(source, "MCP_EVALUATE_MAX_ATTEMPTS", 30, 1, 200),
        evaluate_max_seconds=_int(source, "MCP_EVALUATE_MAX_SECONDS", 90, 5, 300),
        evaluate_call_timeout_seconds=_float(source, "MCP_EVALUATE_CALL_TIMEOUT_SECONDS", 45.0, 1.0, 300.0),
        evaluate_max_concurrent=_int(source, "MCP_EVALUATE_MAX_CONCURRENT", 2, 1, 16),
        tool_timeout_seconds=_float(source, "MCP_TOOL_TIMEOUT_SECONDS", 120.0, 1.0, 600.0),
        max_concurrent_requests=_int(source, "MCP_MAX_CONCURRENT_REQUESTS", 200, 1, 10_000),
        body_read_timeout_seconds=_float(source, "MCP_BODY_READ_TIMEOUT_SECONDS", 30.0, 1.0, 300.0),
        token_default_ttl_days=default_ttl,
        token_max_ttl_days=max_ttl,
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    return Settings(
        voice=load_voice_settings(source),
        evaluation=load_evaluation_settings(source),
        mcp=load_mcp_settings(source),
    )
