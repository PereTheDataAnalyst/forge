"""Outbound request policy (SSRF defence).

Forge only calls hosts that an operator has explicitly allowlisted. User input is
never used to build a URL. Even for allowlisted hosts the resolved addresses are
checked, link-local and metadata addresses are always refused, and private or
loopback addresses are refused unless the operator opted in for local development.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

from ..errors import ErrorCode, ForgeError

Resolver = Callable[[str, int], list[str]]


@dataclass(frozen=True)
class OutboundTarget:
    scheme: str
    host: str
    port: int
    path: str
    addresses: tuple[str, ...]


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def _blocked(address: str, allow_private: bool) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if not ip.is_global and not allow_private:
        return True
    return False


def _refuse(reason: str, message: str) -> ForgeError:
    return ForgeError(
        ErrorCode.OUTBOUND_BLOCKED,
        message,
        http_status=502,
        details={"reason": reason},
    )


def validate_outbound_url(
    url: str,
    *,
    allowed_hosts: Iterable[str],
    allow_private: bool = False,
    allow_insecure_http: bool = False,
    resolver: Resolver | None = None,
) -> OutboundTarget:
    try:
        parts = urlsplit(url)
        port_number = parts.port
    except ValueError:
        raise _refuse("malformed_url", "The outbound target URL is malformed.") from None

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise _refuse("scheme", "Only http and https outbound targets are allowed.")
    if parts.username is not None or parts.password is not None:
        raise _refuse("credentials_in_url", "Credentials in outbound URLs are not allowed.")
    host = (parts.hostname or "").lower()
    if not host:
        raise _refuse("no_host", "The outbound target URL has no host.")

    allowed = {h.strip().lower() for h in allowed_hosts if h and h.strip()}
    if not allowed:
        raise _refuse("no_allowlist", "No outbound host allowlist is configured.")
    if host not in allowed:
        raise _refuse("host_not_allowed", "The outbound target host is not allowlisted.")
    if scheme == "http" and not (allow_insecure_http or allow_private):
        raise _refuse("insecure_http", "Plain http outbound targets are not allowed here.")

    port = port_number or (443 if scheme == "https" else 80)
    try:
        addresses = (resolver or _default_resolver)(host, port)
    except OSError:
        raise _refuse("unresolvable", "The outbound target host could not be resolved.") from None
    if not addresses:
        raise _refuse("unresolvable", "The outbound target host could not be resolved.")
    for address in addresses:
        if _blocked(address, allow_private):
            raise _refuse("blocked_address", "The outbound target address is not permitted.")

    return OutboundTarget(
        scheme=scheme,
        host=host,
        port=port,
        path=parts.path or "/",
        addresses=tuple(addresses),
    )
