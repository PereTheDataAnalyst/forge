"""Settings for the ASR worker. Environment-driven, no secret has a default."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Mapping

from ..config import MIB, _bool, _csv, _float, _int, _text
from ..errors import ConfigError
from ..natlas_facts import ASR_MODELS

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
MIN_TOKEN_CHARS = 24


@dataclass(frozen=True)
class AsrServiceSettings:
    host: str = "127.0.0.1"
    port: int = 8091
    token: str | None = field(default=None, repr=False)
    allow_unauthenticated_loopback: bool = False
    max_upload_bytes: int = 10 * MIB
    max_seconds: int = 30
    decode_timeout_seconds: float = 30.0
    inference_timeout_seconds: float = 120.0
    max_concurrency: int = 1
    max_waiting: int = 8
    temp_dir: str = ""
    device: str = "auto"
    preload_languages: tuple[str, ...] = ()
    processor_overrides: tuple[tuple[str, str], ...] = ()
    ffmpeg_path: str = "ffmpeg"

    @property
    def authentication_required(self) -> bool:
        return self.token is not None

    def overrides_dict(self) -> dict[str, str]:
        return dict(self.processor_overrides)


def _parse_overrides(raw: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in raw:
        language, sep, repo = item.partition("=")
        language, repo = language.strip().lower(), repo.strip()
        if not sep or language not in ASR_MODELS:
            raise ConfigError("ASR_PROCESSOR_OVERRIDES entries must look like ha=owner/repository with ha, ig, yo or en.")
        if not _REPO_ID_RE.match(repo):
            raise ConfigError("ASR_PROCESSOR_OVERRIDES contains an invalid repository id.")
        result.append((language, repo))
    return tuple(result)


def load_asr_service_settings(env: Mapping[str, str] | None = None) -> AsrServiceSettings:
    source = os.environ if env is None else env
    host = _text(source, "ASR_HOST") or "127.0.0.1"
    token = _text(source, "ASR_SERVICE_TOKEN")
    allow_anon = _bool(source, "ASR_ALLOW_UNAUTHENTICATED_LOOPBACK", False)

    if token is not None and len(token) < MIN_TOKEN_CHARS:
        raise ConfigError(
            f"ASR_SERVICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if token is None:
        if not (allow_anon and host.lower() in _LOOPBACK_HOSTS):
            raise ConfigError(
                "ASR_SERVICE_TOKEN is required. Anonymous access is only possible on a loopback host with "
                "ASR_ALLOW_UNAUTHENTICATED_LOOPBACK=true, for local development."
            )

    preload = tuple(code.lower() for code in _csv(source, "ASR_PRELOAD_LANGUAGES"))
    for code in preload:
        if code not in ASR_MODELS:
            raise ConfigError("ASR_PRELOAD_LANGUAGES may only contain ha, ig, yo and en.")

    device = (_text(source, "ASR_DEVICE") or "auto").lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ConfigError("ASR_DEVICE must be auto, cpu or cuda.")

    temp_dir = _text(source, "ASR_TEMP_DIR") or os.path.join(tempfile.gettempdir(), "forge-asr")
    return AsrServiceSettings(
        host=host,
        port=_int(source, "ASR_PORT", 8091, 1, 65535),
        token=token,
        allow_unauthenticated_loopback=allow_anon,
        max_upload_bytes=_int(source, "ASR_MAX_UPLOAD_BYTES", 10 * MIB, 1024, 100 * MIB),
        max_seconds=_int(source, "ASR_MAX_SECONDS", 30, 1, 30),
        decode_timeout_seconds=_float(source, "ASR_DECODE_TIMEOUT_SECONDS", 30.0, 1.0, 300.0),
        inference_timeout_seconds=_float(source, "ASR_INFERENCE_TIMEOUT_SECONDS", 120.0, 1.0, 900.0),
        max_concurrency=_int(source, "ASR_MAX_CONCURRENCY", 1, 1, 16),
        max_waiting=_int(source, "ASR_MAX_WAITING", 8, 0, 256),
        temp_dir=temp_dir,
        device=device,
        preload_languages=preload,
        processor_overrides=_parse_overrides(_csv(source, "ASR_PROCESSOR_OVERRIDES")),
        ffmpeg_path=_text(source, "FFMPEG_PATH") or "ffmpeg",
    )
