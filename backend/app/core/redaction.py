"""Redaction of secrets from log messages and structured log fields (standard library only)."""

import re
from typing import Any

REDACTED = "[REDACTED]"
_MAX_DEPTH = 6

# Forge API keys look like nfk_<10 hex>_<secret>. The public prefix is kept for support.
_API_KEY = re.compile(r"\b(nfk_[0-9a-f]{10})_[A-Za-z0-9_-]{16,}")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_HF_TOKEN = re.compile(r"\bhf_[A-Za-z0-9]{16,}")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@")
_COOKIE_PAIR = re.compile(r"(?i)\b((?:__host-)?forge_(?:session|csrf))=[^;\s]+")

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|authorization|api[_-]?key|cookie|csrf|pepper|credential"
    r"|private[_-]?key|(^|[_-])(access|refresh|auth|bearer|session|inference|hf|id)[_-]?token($|[_-])"
    r"|^token$)",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    """Remove API keys, bearer tokens, Hugging Face tokens and URL credentials from text."""
    if not value:
        return value
    value = _API_KEY.sub(r"\1_" + REDACTED, value)
    value = _BEARER.sub(r"\1 " + REDACTED, value)
    value = _HF_TOKEN.sub("hf_" + REDACTED, value)
    value = _URL_CREDENTIALS.sub(r"\1:" + REDACTED + "@", value)
    value = _COOKIE_PAIR.sub(r"\1=" + REDACTED, value)
    return value


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def redact_mapping(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a structure. Sensitive keys are masked entirely."""
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact_mapping(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_mapping(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
