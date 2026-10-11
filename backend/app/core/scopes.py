"""Credential scopes recognised by the gateway.

Extension modules (evaluation, voice, MCP) add their own scopes by calling register_scope()
when they are imported. Scope names use the form "<resource>.<action>".
"""

import re

SCOPE_MODELS_READ = "models.read"
SCOPE_CHAT_CREATE = "chat.create"

GATEWAY_SCOPES: tuple[str, ...] = (SCOPE_MODELS_READ, SCOPE_CHAT_CREATE)
DEFAULT_SCOPES: tuple[str, ...] = GATEWAY_SCOPES
KNOWN_SCOPES: set[str] = set(GATEWAY_SCOPES)

_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,30}\.[a-z][a-z0-9_]{1,30}$")


def register_scope(name: str) -> None:
    if not _SCOPE_PATTERN.match(name):
        raise ValueError(f"Invalid scope name: {name!r}")
    KNOWN_SCOPES.add(name)
