"""Network helpers: client IP resolution behind proxies and Origin allowlisting (standard library only)."""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def resolve_client_ip(
    remote_addr: str | None, forwarded_for: str | None, trusted_proxy_count: int
) -> str:
    """Return the client IP address.

    With N trusted proxies in front of the application, each proxy appends the address it
    received the connection from. The real client is therefore the (N+1)th entry counted
    from the right of the full chain (X-Forwarded-For entries followed by the socket peer).
    Entries added by the client itself sit further left and are ignored.
    """
    peer = remote_addr or "unknown"
    if trusted_proxy_count <= 0 or not forwarded_for:
        return peer
    hops = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    chain = hops + [peer]
    index = max(len(chain) - (trusted_proxy_count + 1), 0)
    candidate = chain[index]
    return candidate if _valid_ip(candidate) else peer


def normalize_origin(value: str | None) -> str | None:
    """Reduce a URL or Origin header to scheme://host[:port] in lowercase, or None if invalid."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parts.scheme == "https" else 80
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parts.scheme}://{host}{suffix}"


def origin_allowed(origin: str | None, allowed: Iterable[str]) -> bool:
    normalized = normalize_origin(origin)
    return normalized is not None and normalized in set(allowed)
