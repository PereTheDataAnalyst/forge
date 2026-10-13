"""Scoped token generation and verification.

Token format: ``<kind>_<prefix>_<secret>``

  * ``kind``   short lowercase label (``fmcp`` for MCP client tokens)
  * ``prefix`` 8 hex characters, stored in clear, used for lookup and display
  * ``secret`` 43 URL-safe characters (256 bits of entropy), never stored

Only an HMAC-SHA256 of the full token (keyed with a server-side pepper) is stored.
Verification is constant-time. Tokens are shown once at creation.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from ..errors import ConfigError

TOKEN_KIND_MCP = "fmcp"
_TOKEN_RE = re.compile(r"^(?P<kind>[a-z]{2,8})_(?P<prefix>[0-9a-f]{8})_(?P<secret>[A-Za-z0-9_-]{43})$")
MIN_PEPPER_BYTES = 32


@dataclass(frozen=True)
class GeneratedToken:
    token: str
    prefix: str


@dataclass(frozen=True)
class ParsedToken:
    kind: str
    prefix: str


def generate_token(kind: str = TOKEN_KIND_MCP) -> GeneratedToken:
    if not re.fullmatch(r"[a-z]{2,8}", kind):
        raise ValueError("kind must be 2-8 lowercase letters")
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return GeneratedToken(token=f"{kind}_{prefix}_{secret}", prefix=prefix)


def parse_token(token: str) -> ParsedToken | None:
    """Return kind and prefix when the token is well formed, otherwise ``None``."""
    if not isinstance(token, str) or len(token) > 128:
        return None
    match = _TOKEN_RE.fullmatch(token)
    if not match:
        return None
    return ParsedToken(kind=match["kind"], prefix=match["prefix"])


class TokenHasher:
    def __init__(self, pepper: str | bytes) -> None:
        raw = pepper.encode("utf-8") if isinstance(pepper, str) else bytes(pepper)
        if len(raw) < MIN_PEPPER_BYTES:
            raise ConfigError(
                f"The token pepper must be at least {MIN_PEPPER_BYTES} bytes. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        self._pepper = raw

    def hash(self, token: str) -> str:
        return hmac.new(self._pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, token: str, expected_hex: str) -> bool:
        try:
            return hmac.compare_digest(self.hash(token), expected_hex)
        except (TypeError, ValueError):
            return False

    def burn(self, token: str) -> None:
        """Spend the same work as a real verification. Used when the prefix is unknown."""
        self.verify(token, "0" * 64)
