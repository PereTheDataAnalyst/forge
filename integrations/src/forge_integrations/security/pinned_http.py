"""HTTP and HTTPS connections pinned to already-validated IP addresses.

``validate_outbound_url`` resolves a hostname and checks every address it returns. If the
caller then connected by hostname, the operating system would resolve it a second time and
a hostile DNS server could answer with a different, internal address (DNS rebinding).

The classes here connect only to the addresses that were validated. The original hostname
is still used for the ``Host`` header, the TLS server name (SNI) and certificate
verification, so TLS protection is unchanged.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from typing import Sequence

from .outbound import OutboundTarget


def _connect_to_pinned_addresses(
    addresses: Sequence[str],
    port: int,
    timeout: float | None,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    last_error: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout, source_address)
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("no validated address to connect to")


class PinnedHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` that never performs its own DNS lookup."""

    def __init__(self, host: str, port: int, *, addresses: Sequence[str], timeout: float = 60.0) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_addresses = tuple(addresses)
        if not self._pinned_addresses:
            raise ValueError("at least one validated address is required")
        # ``_create_connection`` is the documented hook http.client exposes for replacing
        # how the socket is opened. The hostname argument it receives is ignored on purpose.
        self._create_connection = self._open_pinned

    def _open_pinned(
        self,
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        return _connect_to_pinned_addresses(self._pinned_addresses, address[1], timeout, source_address)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """``HTTPSConnection`` pinned to validated addresses, verifying the certificate for the hostname."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        addresses: Sequence[str],
        timeout: float = 60.0,
        context: ssl.SSLContext | None = None,
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context or ssl.create_default_context())
        self._pinned_addresses = tuple(addresses)
        if not self._pinned_addresses:
            raise ValueError("at least one validated address is required")
        self._create_connection = self._open_pinned

    def _open_pinned(
        self,
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        return _connect_to_pinned_addresses(self._pinned_addresses, address[1], timeout, source_address)


def open_pinned_connection(
    target: OutboundTarget, *, timeout: float = 60.0, context: ssl.SSLContext | None = None
) -> http.client.HTTPConnection:
    """Create a connection for a validated target. The caller owns closing it."""
    if target.scheme == "https":
        return PinnedHTTPSConnection(target.host, target.port, addresses=target.addresses, timeout=timeout, context=context)
    return PinnedHTTPConnection(target.host, target.port, addresses=target.addresses, timeout=timeout)
