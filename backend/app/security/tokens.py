"""API key, session token and CSRF token primitives (standard library only).

API key format:  nfk_<10 hex characters>_<43 URL-safe base64 characters>
  - "nfk_<10 hex>" is the public prefix. It is stored in clear text and shown in the UI.
  - The secret part carries 256 bits of entropy.
  - Only an HMAC-SHA-256 of the full key, keyed with a server-side pepper, is stored.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

API_KEY_LABEL = "nfk"
HASH_ALGORITHM = "hmac-sha256-v1"
_API_KEY_PATTERN = re.compile(r"^(nfk_[0-9a-f]{10})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    full_key: str
    prefix: str


def generate_api_key() -> GeneratedApiKey:
    public_id = secrets.token_hex(5)
    secret = secrets.token_urlsafe(32)
    prefix = f"{API_KEY_LABEL}_{public_id}"
    return GeneratedApiKey(full_key=f"{prefix}_{secret}", prefix=prefix)


def parse_api_key(raw: str) -> str | None:
    """Return the public prefix if the string is a well-formed Forge API key, else None."""
    match = _API_KEY_PATTERN.fullmatch(raw)
    return match.group(1) if match else None


def hash_api_key(full_key: str, pepper: bytes) -> str:
    return hmac.new(pepper, full_key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_api_key(full_key: str, stored_hash: str, pepper: bytes) -> bool:
    return hmac.compare_digest(hash_api_key(full_key, pepper), stored_hash)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_for_session(session_token_hash: str, secret_key: bytes) -> str:
    """Derive the CSRF token for a session. It cannot be guessed without the server secret."""
    mac = hmac.new(
        secret_key, b"forge-csrf-v1:" + session_token_hash.encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def verify_csrf_token(candidate: str, session_token_hash: str, secret_key: bytes) -> bool:
    expected = csrf_token_for_session(session_token_hash, secret_key)
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
