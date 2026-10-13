"""Host and Origin allow-lists.

``Host`` validation stops DNS rebinding: a web page cannot make a victim's browser talk to a
service on localhost or an internal address through a hostname the attacker controls, because the
service only answers to the names its operator listed. ``Origin`` validation decides which web
pages may call the service from a browser. Neither is a substitute for authentication.

Rules, written once so configuration, the HTTP guard and the tests agree:

  * a host rule is ``name`` (any port, or none), ``name:port`` (exactly that port) or ``name:*``;
  * names are compared case-insensitively, IPv6 literals are written in brackets;
  * an origin rule is ``scheme://name`` or ``scheme://name:port`` with scheme http or https;
  * there is no wildcard origin and no wildcard host name.
"""

from __future__ import annotations

import re
from typing import Iterable

LOOPBACK_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")

_NAME = r"(?:\[[0-9a-f:.]{2,45}\]|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)"
_HOST_RE = re.compile(rf"^(?P<host>{_NAME})(?::(?P<port>[0-9]{{1,5}}|\*))?$")
_ORIGIN_RE = re.compile(rf"^(?P<scheme>https?)://(?P<host>{_NAME})(?::(?P<port>[0-9]{{1,5}}))?$")


def _valid_port(port: str | None) -> bool:
    return port is None or port == "*" or (port.isdigit() and 0 < int(port) <= 65535)


def parse_host_rule(value: str) -> tuple[str, str | None]:
    """Parse an allow-list entry. Raises ``ValueError`` with an operator-readable message."""
    match = _HOST_RE.match(value.strip().lower()) if isinstance(value, str) else None
    if not match or not _valid_port(match["port"]):
        raise ValueError(f"{value!r} is not a valid host entry. Use name, name:port or name:*, for example mcp.example.com.")
    return match["host"], match["port"]


def parse_origin_rule(value: str) -> str:
    normalised = normalise_origin(value)
    if normalised is None:
        raise ValueError(
            f"{value!r} is not a valid origin. Use scheme://host or scheme://host:port with http or https. "
            "Wildcards and paths are not allowed."
        )
    return normalised


def normalise_origin(value: str | None) -> str | None:
    """Canonical form of an Origin header or rule, or ``None`` when it is not a plain http(s) origin."""
    if not isinstance(value, str):
        return None
    match = _ORIGIN_RE.match(value.strip().lower())
    if not match or not _valid_port(match["port"]):
        return None
    port = f":{match['port']}" if match["port"] else ""
    return f"{match['scheme']}://{match['host']}{port}"


class HostPolicy:
    """Allow-list for the ``Host`` request header."""

    def __init__(self, entries: Iterable[str] = ()) -> None:
        rules = [parse_host_rule(entry) for entry in entries]
        if not rules:
            rules = [(host, None) for host in LOOPBACK_HOSTS]
        self.rules: tuple[tuple[str, str | None], ...] = tuple(dict.fromkeys(rules))

    def allows(self, header: str | None) -> bool:
        if not header or len(header) > 300:
            return False
        match = _HOST_RE.match(header.strip().lower())
        if not match or match["port"] == "*" or not _valid_port(match["port"]):
            return False
        host, port = match["host"], match["port"]
        for rule_host, rule_port in self.rules:
            if rule_host == host and (rule_port is None or rule_port == "*" or rule_port == port):
                return True
        return False

    def sdk_patterns(self) -> list[str]:
        """The same rules in the MCP SDK's own notation (exact values and ``name:*``)."""
        patterns: list[str] = []
        for host, port in self.rules:
            if port is None:
                patterns.extend([host, f"{host}:*"])
            elif port == "*":
                patterns.extend([f"{host}:*"])
            else:
                patterns.append(f"{host}:{port}")
        return list(dict.fromkeys(patterns))


class OriginPolicy:
    """Allow-list for the ``Origin`` request header. An absent Origin (not a browser) is allowed."""

    def __init__(self, entries: Iterable[str] = ()) -> None:
        self.origins: frozenset[str] = frozenset(parse_origin_rule(entry) for entry in entries)

    def allows(self, header: str | None) -> bool:
        if header is None:
            return True
        normalised = normalise_origin(header)
        return normalised is not None and normalised in self.origins

    def matches(self, header: str | None) -> str | None:
        """The canonical allowed origin for a header, or ``None``. Used to build CORS responses."""
        normalised = normalise_origin(header)
        return normalised if normalised in self.origins else None
