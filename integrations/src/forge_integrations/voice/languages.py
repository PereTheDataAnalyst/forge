"""Language resolution for the official N-ATLAS ASR models."""

from __future__ import annotations

from ..errors import ErrorCode, ForgeError
from ..natlas_facts import ASR_MODELS, AsrFacts

_ALIASES = {
    "english": "en",
    "nigerian english": "en",
    "hausa": "ha",
    "igbo": "ig",
    "yoruba": "yo",
}


def resolve_asr_language(value: object) -> AsrFacts:
    """Map a language code or name to its official ASR model. Raises ``ForgeError`` if unsupported."""
    if isinstance(value, str):
        key = value.strip().lower()
        key = _ALIASES.get(key, key)
        if key in ASR_MODELS:
            return ASR_MODELS[key]
    raise ForgeError(
        ErrorCode.LANGUAGE_UNSUPPORTED,
        "Unsupported language. Official N-ATLAS ASR models exist for: " + ", ".join(sorted(ASR_MODELS)) + ".",
        http_status=422,
        details={"supported": sorted(ASR_MODELS)},
    )
