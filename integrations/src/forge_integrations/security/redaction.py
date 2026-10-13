"""Log and error redaction.

Rules:
  * values stored under sensitive keys are replaced entirely;
  * well-known secret shapes inside strings are replaced;
  * long strings are truncated so logs stay bounded;
  * raw bytes (for example audio) are never rendered, only their length.

All patterns are linear-time (no nested quantifiers) so redaction cannot be used
to stall a worker.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Pattern

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "x_api_key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "pepper",
        "private_key",
        "token_hash",
        "audio",
        "audio_base64",
        "audio_bytes",
        "content_base64",
    }
)
_SENSITIVE_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_private_key",
)

_LABELLED_PATTERNS: tuple[tuple[str, Pattern[str], str], ...] = (
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (
        "forge_token",
        re.compile(r"\bf(?:k|mcp)_[A-Za-z0-9]{4,12}_[A-Za-z0-9_-]{16,}"),
        "[REDACTED_FORGE_TOKEN]",
    ),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "[REDACTED_HF_TOKEN]"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED_KEY]"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "[REDACTED_JWT]",
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY_HEADER]",
    ),
)
_DEFAULT_PATTERNS: tuple[tuple[Pattern[str], str], ...] = tuple(
    (pattern, replacement) for _label, pattern, replacement in _LABELLED_PATTERNS
)


def find_secret_labels(text: str) -> list[str]:
    """Names of the secret shapes present in ``text`` (used by the no_secrets check)."""
    return [label for label, pattern, _ in _LABELLED_PATTERNS if pattern.search(text)]


def _is_sensitive_key(key: str) -> bool:
    k = key.strip().lower().replace("-", "_")
    return k in _SENSITIVE_KEYS or k.endswith(_SENSITIVE_SUFFIXES)


class Redactor:
    def __init__(
        self,
        extra_patterns: Iterable[tuple[Pattern[str], str]] = (),
        *,
        max_string_chars: int = 2000,
        max_depth: int = 8,
        max_items: int = 200,
    ) -> None:
        self._patterns = tuple(_DEFAULT_PATTERNS) + tuple(extra_patterns)
        self._max_string_chars = max_string_chars
        self._max_depth = max_depth
        self._max_items = max_items

    def text(self, value: str) -> str:
        out = value
        for pattern, replacement in self._patterns:
            out = pattern.sub(replacement, out)
        if len(out) > self._max_string_chars:
            extra = len(out) - self._max_string_chars
            out = f"{out[: self._max_string_chars]}...[truncated {extra} chars]"
        return out

    def value(self, obj: Any, _depth: int = 0) -> Any:
        if _depth > self._max_depth:
            return "[MAX_DEPTH]"
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return f"<{len(obj)} bytes>"
        if isinstance(obj, Mapping):
            result: dict[str, Any] = {}
            for index, (key, item) in enumerate(obj.items()):
                if index >= self._max_items:
                    result["..."] = f"{len(obj) - index} more keys"
                    break
                key_text = str(key)
                result[key_text] = REDACTED if _is_sensitive_key(key_text) else self.value(item, _depth + 1)
            return result
        if isinstance(obj, (list, tuple, set, frozenset)):
            items = list(obj)
            shown = [self.value(item, _depth + 1) for item in items[: self._max_items]]
            if len(items) > self._max_items:
                shown.append(f"...{len(items) - self._max_items} more items")
            return shown
        return f"<{type(obj).__name__}>"


default_redactor = Redactor()


def redact(obj: Any) -> Any:
    """Return a redacted deep copy. Never mutates the input."""
    return default_redactor.value(obj)


def redact_text(value: str) -> str:
    return default_redactor.text(value)
