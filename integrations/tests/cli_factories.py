"""Importable factories for the command line tests (``MCP_*_FACTORY=tests.cli_factories:name``)."""

from __future__ import annotations

from forge_integrations.mcp.ratelimit import InMemoryRateLimiter

from .helpers import FakeGateway
from .mcp_helpers import FakeProjects, FakeUsage


def make_gateway():
    return FakeGateway("cli gateway reply")


def make_usage():
    return FakeUsage()


def make_projects():
    return FakeProjects()


def make_limiter():
    return InMemoryRateLimiter()


def broken_factory():
    raise RuntimeError("password=hunter2 host=db.internal")


def wrong_shape():
    return object()


not_callable = 5
