"""Registration, authentication and session management."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.rate_limit import RateLimiter
from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, RateLimited, Unauthorized, UnprocessableEntity
from app.db.base import utcnow
from app.db.models.user import User, UserSession
from app.security.password_policy import validate_password
from app.security.passwords import (
    burn_verification_time,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.security.tokens import generate_session_token, hash_session_token
from app.services.audit import record_audit


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None
    request_id: str | None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
    client: ClientInfo,
) -> User:
    email = normalize_email(email)
    problems = validate_password(password, email)
    if problems:
        raise UnprocessableEntity(
            "The password does not meet the requirements.",
            code="weak_password",
            details=[{"field": "password", "message": problem} for problem in problems],
        )
    existing = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise Conflict("An account with this email already exists.", code="email_already_registered")

    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(email=email, password_hash=password_hash, display_name=display_name or None)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict(
            "An account with this email already exists.", code="email_already_registered"
        ) from exc
    record_audit(
        db,
        action="auth.register",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return user


async def authenticate(
    db: AsyncSession,
    limiter: RateLimiter,
    settings: Settings,
    *,
    email: str,
    password: str,
    client: ClientInfo,
) -> User:
    """Verify credentials with lockout and timing protection. Raises on any failure."""
    email = normalize_email(email)
    window = settings.login_lockout_minutes * 60
    account_key = f"auth:fail:acct:{_fingerprint(email)}"
    ip_key = f"auth:fail:ip:{client.ip or 'unknown'}"

    if await limiter.count(account_key) >= settings.login_max_failures:
        retry = await limiter.ttl(account_key) or window
        raise RateLimited(
            "Too many failed sign-in attempts for this account. Try again later.",
            code="account_temporarily_locked",
            headers={"Retry-After": str(retry)},
        )
    if await limiter.count(ip_key) >= settings.login_max_failures * 4:
        retry = await limiter.ttl(ip_key) or window
        raise RateLimited(
            "Too many failed sign-in attempts from this network. Try again later.",
            code="ip_temporarily_locked",
            headers={"Retry-After": str(retry)},
        )

    async def fail(user: User | None) -> None:
        await limiter.increment(account_key, window)
        await limiter.increment(ip_key, window)
        if user is not None:
            record_audit(
                db,
                action="auth.login_failed",
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                ip_address=client.ip,
                user_agent=client.user_agent,
                request_id=client.request_id,
            )
            await db.commit()
        raise Unauthorized("The email or password is incorrect.", code="invalid_credentials")

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        await asyncio.to_thread(burn_verification_time, password)
        await fail(None)
        raise AssertionError("unreachable")  # pragma: no cover
    if not await asyncio.to_thread(verify_password, user.password_hash, password):
        await fail(user)
        raise AssertionError("unreachable")  # pragma: no cover
    if not user.is_active:
        raise Forbidden("This account is disabled.", code="account_disabled")

    await limiter.reset(account_key)
    if await asyncio.to_thread(needs_rehash, user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
    user.last_login_at = utcnow()
    return user


async def create_session(
    db: AsyncSession, settings: Settings, user: User, client: ClientInfo, *, audit_action: str
) -> tuple[str, UserSession]:
    """Create a session and return (raw_token, row). The raw token exists only in the cookie."""
    token = generate_session_token()
    now = utcnow()
    session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_ttl_hours),
        ip_address=client.ip,
        user_agent=(client.user_agent or "")[:256] or None,
    )
    db.add(session)
    record_audit(
        db,
        action=audit_action,
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
    return token, session


async def load_session(
    db: AsyncSession, raw_token: str, settings: Settings
) -> tuple[User, UserSession] | None:
    now = utcnow()
    idle_cutoff = now - timedelta(hours=settings.session_idle_ttl_hours)
    statement = (
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == hash_session_token(raw_token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at > idle_cutoff,
            User.is_active.is_(True),
        )
    )
    row = (await db.execute(statement)).first()
    if row is None:
        return None
    session, user = row
    if now - session.last_seen_at > timedelta(seconds=60):
        session.last_seen_at = now
        await db.commit()
    return user, session


async def revoke_session(db: AsyncSession, session: UserSession, client: ClientInfo) -> None:
    session.revoked_at = utcnow()
    record_audit(
        db,
        action="auth.logout",
        actor_user_id=session.user_id,
        target_type="session",
        target_id=str(session.id),
        ip_address=client.ip,
        user_agent=client.user_agent,
        request_id=client.request_id,
    )
    await db.commit()
