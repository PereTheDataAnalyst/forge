"""FastAPI dependencies: settings, database, Redis, authentication and CSRF.

Cookie-session authentication enforces CSRF and Origin checks for every state-changing
method inside `get_auth_context`, so an individual route cannot forget them.
API key authentication uses a Bearer header, which browsers never attach automatically,
so CSRF protection does not apply to it.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.rate_limit import RateLimiter
from app.core.config import Settings
from app.core.errors import Forbidden, Unauthorized
from app.core.net import origin_allowed
from app.core.request_context import annotate, get_client_ip, get_request_id
from app.db.models.user import User, UserSession
from app.integrations.contracts import GatewayPrincipal, IntegrationRegistry
from app.providers.base import ModelProvider
from app.security.tokens import csrf_token_for_session, verify_csrf_token
from app.services import api_key_service, auth_service
from app.services.auth_service import ClientInfo
from app.services.gateway_service import GatewayService

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="natlas-forge"'}


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_provider(request: Request) -> ModelProvider:
    return request.app.state.provider


def get_gateway(request: Request) -> GatewayService:
    return request.app.state.gateway


def get_integrations(request: Request) -> IntegrationRegistry:
    return request.app.state.integrations


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        ip=get_client_ip(),
        user_agent=request.headers.get("user-agent"),
        request_id=get_request_id(),
    )


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ProviderDep = Annotated[ModelProvider, Depends(get_provider)]
GatewayDep = Annotated[GatewayService, Depends(get_gateway)]
IntegrationsDep = Annotated[IntegrationRegistry, Depends(get_integrations)]
ClientDep = Annotated[ClientInfo, Depends(get_client_info)]


def check_origin(request: Request, settings: Settings) -> None:
    """Reject browser requests that come from an origin outside the allowlist."""
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(origin, settings.allowed_origins):
        raise Forbidden("This origin is not allowed to call the API.", code="origin_not_allowed")


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: UserSession
    csrf_token: str


async def get_auth_context(request: Request, db: DbDep, settings: SettingsDep) -> AuthContext:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise Unauthorized("Sign in to continue.", code="not_authenticated")
    found = await auth_service.load_session(db, raw_token, settings)
    if found is None:
        raise Unauthorized("Your session has expired. Sign in again.", code="session_expired")
    user, session = found
    secret = settings.secret_key.get_secret_value().encode("utf-8")
    csrf_token = csrf_token_for_session(session.token_hash, secret)

    if request.method not in SAFE_METHODS:
        check_origin(request, settings)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not verify_csrf_token(supplied, session.token_hash, secret):
            raise Forbidden(
                "The CSRF token is missing or invalid.", code="csrf_validation_failed"
            )

    annotate(user_id=str(user.id))
    return AuthContext(user=user, session=session, csrf_token=csrf_token)


AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


async def get_api_principal(
    request: Request, db: DbDep, settings: SettingsDep
) -> GatewayPrincipal:
    header = request.headers.get("authorization")
    if not header:
        raise Unauthorized(
            "Missing API key. Send it as 'Authorization: Bearer <key>'.",
            code="missing_api_key",
            headers=BEARER_CHALLENGE,
        )
    scheme, _, credential = header.partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential:
        raise Unauthorized(
            "The Authorization header must use the Bearer scheme.",
            code="invalid_api_key",
            headers=BEARER_CHALLENGE,
        )
    principal = await api_key_service.authenticate_api_key(db, settings, credential)
    annotate(project_id=str(principal.project_id), api_key_prefix=principal.api_key_prefix)
    return principal


PrincipalDep = Annotated[GatewayPrincipal, Depends(get_api_principal)]
