"""What an MCP client can be allowed to do.

The MCP library decides tool visibility by scope, and several tools share a scope (generate, chat
and translate all need `generate`). So access is granted in whole groups: one group is one library
scope and the tools that scope unlocks. A client's `allowed_tools` column lists the tools in the
groups it was given, and its scopes are read back from that list, so the two cannot disagree.

No group is administrative. Nothing here can create keys, change a project or read request content.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from forge_integrations.mcp.scopes import (
    ALL_SCOPES,
    SCOPE_EVALUATE,
    SCOPE_GENERATE,
    SCOPE_MODELS_READ,
    SCOPE_PROJECT_READ,
    SCOPE_TRANSCRIBE,
    SCOPE_USAGE_READ,
)

# A platform API key must carry this scope to be accepted by the MCP endpoint. It grants no
# access to the REST gateway, which needs models.read or chat.create.
INVOKE_SCOPE = "mcp.invoke"
MCP_PATH = "/mcp"
MAX_CLIENTS_PER_PROJECT = 10
# The library's own tokens last 30 days by default and at most 90. Platform keys can live longer,
# so the limit is kept here: an MCP client key is short-lived on purpose, and is replaced, not renewed.
EXPIRY_CHOICES_DAYS: tuple[int, ...] = (30, 90)
DEFAULT_EXPIRY_DAYS = 30


@dataclass(frozen=True, slots=True)
class AccessGroup:
    scope: str
    label: str
    description: str
    tools: tuple[str, ...]
    read_only: bool


ACCESS_GROUPS: tuple[AccessGroup, ...] = (
    AccessGroup(
        SCOPE_MODELS_READ,
        "Model information",
        "Read verified facts about the N-ATLAS models.",
        ("get_model_info",),
        True,
    ),
    AccessGroup(
        SCOPE_PROJECT_READ,
        "Project information",
        "Read this project's name and the limits that apply to it.",
        ("get_project_info",),
        True,
    ),
    AccessGroup(
        SCOPE_USAGE_READ,
        "Usage summary",
        "Read request counts and latency for this project. Never request content.",
        ("get_usage",),
        True,
    ),
    AccessGroup(
        SCOPE_GENERATE,
        "Text generation",
        "Generate text, chat and translate with N-ATLAS. Counts against this project's limits.",
        ("generate", "chat", "translate"),
        False,
    ),
    AccessGroup(
        SCOPE_TRANSCRIBE,
        "Speech transcription",
        "Transcribe a short audio clip. Needs a speech worker to be connected.",
        ("transcribe",),
        False,
    ),
    AccessGroup(
        SCOPE_EVALUATE,
        "Small evaluations",
        "Run a small developer-defined evaluation against the model.",
        ("evaluate",),
        False,
    ),
)

DEFAULT_ACCESS: tuple[str, ...] = (SCOPE_MODELS_READ, SCOPE_PROJECT_READ)
ALL_TOOLS: tuple[str, ...] = tuple(tool for group in ACCESS_GROUPS for tool in group.tools)

_BY_SCOPE = {group.scope: group for group in ACCESS_GROUPS}


def normalise_access(values: Iterable[str]) -> list[str]:
    """Validate requested access groups. Returns them de-duplicated in catalogue order.

    Raises ValueError that names every unknown value, so a typo is never silently dropped.
    """
    wanted = {value.strip() for value in values if isinstance(value, str)}
    unknown = sorted(wanted - set(_BY_SCOPE))
    if unknown:
        raise ValueError("Unknown access: " + ", ".join(unknown))
    if not wanted:
        raise ValueError("Choose at least one kind of access.")
    return [group.scope for group in ACCESS_GROUPS if group.scope in wanted]


def tools_for(access: Iterable[str]) -> list[str]:
    """The tool names unlocked by access groups, in catalogue order."""
    wanted = set(access)
    return [tool for group in ACCESS_GROUPS if group.scope in wanted for tool in group.tools]


def access_for(allowed_tools: Iterable[str]) -> list[str]:
    """The access groups a tool list fully covers. A group counts only if every tool is listed."""
    listed = set(allowed_tools)
    return [group.scope for group in ACCESS_GROUPS if set(group.tools) <= listed]


def scopes_for(allowed_tools: Iterable[str]) -> frozenset[str]:
    """The MCP library scopes for a client, read from its tool list."""
    return frozenset(access_for(allowed_tools)) & ALL_SCOPES
