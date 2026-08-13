"""Authentication endpoints (taskbook §42 Auth, §39)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from aipvs_api.dependencies import CurrentUser, SessionDep, get_request_origin
from aipvs_api.v1.schemas import ApiRequest
from backend_core.config import get_settings
from backend_core.domain.enums import AuditAction
from backend_core.domain.models import User
from backend_core.errors import UnauthorizedError
from backend_core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from backend_core.services.audit import AuditService
from backend_core.services.auth import AuthService, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

# §39 wants the refresh token in an HttpOnly cookie: JavaScript cannot read it,
# so an XSS bug cannot exfiltrate a month-long credential. The short-lived
# access token stays in memory on the client and travels in the Authorization
# header, which keeps it out of CSRF's reach.
REFRESH_COOKIE_NAME: Final[str] = "aipvs_refresh"


class RegisterRequest(ApiRequest):
    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description=f"At least {MIN_PASSWORD_LENGTH} characters.",
    )
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(ApiRequest):
    email: EmailStr
    # No length bounds: rejecting a too-short password here would reveal the
    # policy to an attacker and, worse, tell them a guess was malformed rather
    # than wrong. Validation belongs at registration.
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    avatar_url: str | None = None
    status: str
    created_at: datetime
    last_login_at: datetime | None = None

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            status=user.status.value,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class TokenResponse(BaseModel):
    """Issued credentials.

    The refresh token is **not** in this body — it is set as an HttpOnly cookie.
    Returning it here would put a long-lived credential somewhere JavaScript
    can read it.
    """

    access_token: str
    # S105 is a false positive here: "bearer" is the RFC 6750 token type
    # identifier, not a credential.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds.")
    user: UserResponse


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for rate limiting.

    ``X-Forwarded-For`` is only trusted when a proxy is actually in front of
    the app; taking it unconditionally would let a caller spoof the header and
    sidestep per-IP limits entirely. Wiring the trusted-proxy configuration is
    part of PHASE 23 deployment.
    """
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.jwt_refresh_token_ttl,
        httponly=True,
        # Not Secure in local development, or the browser drops it over http.
        secure=settings.is_production,
        # "lax" still sends the cookie on top-level navigation, which is what
        # a returning user needs, while blocking cross-site POSTs.
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/api/v1/auth")


def _token_response(user: User, tokens: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in=get_settings().jwt_access_token_ttl,
        user=UserResponse.of(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> TokenResponse:
    """Register a user and their personal workspace, then sign them in.

    A user without a workspace cannot use the product at all, so both are
    created in one transaction.
    """
    result = await AuthService(session).register(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        client_ip=_client_ip(request),
    )
    await AuditService(session).record(
        AuditAction.LOGIN,
        workspace_id=result.workspace_id,
        actor_user_id=result.user.id,
        target_type="user",
        target_id=result.user.id,
        origin=get_request_origin(request),
        context={"via": "register"},
    )
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return _token_response(result.user, result.tokens)


@router.post("/login", response_model=TokenResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> TokenResponse:
    """Exchange email and password for tokens.

    Every failure returns the same error regardless of cause, and the
    no-such-user path still performs a password hash, so neither the response
    nor its timing reveals whether an email is registered.
    """
    origin = get_request_origin(request)
    audit = AuditService(session)

    try:
        user, tokens = await AuthService(session).login(
            email=payload.email,
            password=payload.password,
            client_ip=_client_ip(request),
        )
    except Exception:
        # §60 wants failed logins recorded, and this is the only place that
        # knows the attempt happened. The email is *not* in the context: a
        # failed attempt against an address nobody registered would otherwise
        # turn the audit table into a list of addresses somebody guessed.
        await audit.record(
            AuditAction.LOGIN_FAILED,
            target_type="user",
            succeeded=False,
            origin=origin,
        )
        # Committed separately, because the request is about to unwind and the
        # session's transaction will be rolled back with it.
        await session.commit()
        raise

    await audit.record(
        AuditAction.LOGIN,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        origin=origin,
    )
    _set_refresh_cookie(response, tokens.refresh_token)
    return _token_response(user, tokens)


@router.post("/refresh", response_model=TokenResponse, summary="Renew the access token")
async def refresh(
    response: Response,
    session: SessionDep,
    aipvs_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token.

    The presented token is revoked as part of the exchange, so a stolen one is
    usable at most once.
    """
    if not aipvs_refresh:
        raise UnauthorizedError("No active session.")

    service = AuthService(session)
    tokens = await service.refresh(aipvs_refresh)
    user = await service.get_authenticated_user(tokens.access_token)

    _set_refresh_cookie(response, tokens.refresh_token)
    return _token_response(user, tokens)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out",
)
async def logout(
    response: Response,
    session: SessionDep,
    aipvs_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    """Revoke the refresh token and clear the cookie.

    Succeeds even without a valid token: a logout that fails leaves the user
    believing they are signed out when they are not.
    """
    await AuthService(session).logout(aipvs_refresh)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserResponse, summary="The signed-in user")
async def me(user: CurrentUser) -> UserResponse:
    """Return the authenticated user."""
    return UserResponse.of(user)
