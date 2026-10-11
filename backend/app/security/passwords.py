"""Password hashing with Argon2id (argon2-cffi defaults follow RFC 9106 recommendations)."""

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    return PasswordHasher()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher().hash("forge-timing-equalisation-password")


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher().check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def burn_verification_time(password: str) -> None:
    """Spend roughly the time of a real verification so unknown accounts are not distinguishable."""
    verify_password(_dummy_hash(), password)
