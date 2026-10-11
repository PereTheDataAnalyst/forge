"""Account registration and cookie-session authentication."""

from fastapi import APIRouter, Request, Response

from app.api.deps import (
    AuthDep,
    ClientDep,
    DbDep,
    LimiterDep,
    SettingsDep,
    check_origin,
)
from app.core.config import Settings
from app.core.errors import Forbidden
from app.db.models.user import UserSession
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut
from app.schemas.common import ERROR_RESPONSES
from app.security.tokens import csrf_token_for_session
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _csrf_for(settings: Settings, session: UserSession) -> str:
    return csrf_token_for_session(
        session.token_hash, settings.secret_key.get_secret_value().encode("utf-8")
    )


def _set_session_cookies(
    response: Response, settings: Settings, session_token: str, session: UserSession
) -> None:
    max_age = settings.session_absolute_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        _csrf_for(settings, session),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201, responses=ERROR_RESPONSES)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    client: ClientDep,
) -> UserOut:
    """Create an account and sign in."""
    check_origin(request, settings)
    if not settings.registration_enabled:
        raise Forbidden("Registration is currently closed.", code="registration_closed")
    await limiter.enforce(
        f"auth:ip:{client.ip or 'unknown'}",
        settings.auth_rate_limit_per_minute,
        60,
        code="auth_rate_limited",
        message="Too many attempts. Try again in a minute.",
    )
    user = await auth_service.register_user(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        client=client,
    )
    token, session = await auth_service.create_session(
        db, settings, user, client, audit_action="auth.login"
    )
    _set_session_cookies(response, settings, token, session)
    return UserOut.model_validate(user)


@router.post("/login", response_model=UserOut, responses=ERROR_RESPONSES)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    client: ClientDep,
) -> UserOut:
    """Sign in with email and password. Repeated failures lock the account temporarily."""
    check_origin(request, settings)
    await limiter.enforce(
        f"auth:ip:{client.ip or 'unknown'}",
        settings.auth_rate_limit_per_minute,
        60,
        code="auth_rate_limited",
        message="Too many attempts. Try again in a minute.",
    )
    user = await auth_service.authenticate(
        db, limiter, settings, email=payload.email, password=payload.password, client=client
    )
    token, session = await auth_service.create_session(
        db, settings, user, client, audit_action="auth.login"
    )
    _set_session_cookies(response, settings, token, session)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=204, response_class=Response, responses=ERROR_RESPONSES)
async def logout(auth: AuthDep, db: DbDep, settings: SettingsDep, client: ClientDep) -> Response:
    """Revoke the current session and clear its cookies."""
    await auth_service.revoke_session(db, auth.session, client)
    out = Response(status_code=204)
    out.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    out.delete_cookie(
        settings.csrf_cookie_name, path="/", secure=settings.cookie_secure, samesite="lax"
    )
    return out


@router.get("/me", response_model=UserOut, responses=ERROR_RESPONSES)
async def me(
    request: Request, response: Response, auth: AuthDep, settings: SettingsDep
) -> UserOut:
    """Return the signed-in user. Also restores the CSRF cookie if the browser lost it."""
    if request.cookies.get(settings.csrf_cookie_name) != auth.csrf_token:
        response.set_cookie(
            settings.csrf_cookie_name,
            auth.csrf_token,
            max_age=settings.session_absolute_ttl_hours * 3600,
            httponly=False,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )
    return UserOut.model_validate(auth.user)
