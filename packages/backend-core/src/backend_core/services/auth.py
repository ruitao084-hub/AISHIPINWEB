"""Authentication service (taskbook §39).

Owns registration, login, refresh and logout. The API layer translates HTTP;
every decision about whether a credential is acceptable is made here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import UserStatus, WorkspaceRole
from backend_core.domain.models import User
from backend_core.errors import (
    AppError,
    ErrorCode,
    InvalidCredentialsError,
    UnauthorizedError,
    ValidationError,
)
from backend_core.observability import get_logger
from backend_core.repositories.users import UserRepository, normalize_email
from backend_core.repositories.workspaces import WorkspaceRepository
from backend_core.security import (
    LOGIN_ACCOUNT_LIMIT,
    LOGIN_IP_LIMIT,
    REGISTER_IP_LIMIT,
    PasswordPolicyError,
    TokenClaims,
    TokenType,
    check_rate_limit,
    create_access_token,
    create_refresh_token,
    decode_token,
    dummy_verify,
    hash_password,
    is_refresh_token_revoked,
    needs_rehash,
    reset_rate_limit,
    revoke_refresh_token,
    verify_password,
)

logger = get_logger(__name__)


class EmailAlreadyRegisteredError(AppError):
    """That email already has an account."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 409
    default_message = "An account with this email already exists."


@dataclass(frozen=True, slots=True)
class TokenPair:
    """Freshly issued credentials."""

    access_token: str
    refresh_token: str
    access_claims: TokenClaims
    refresh_claims: TokenClaims


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """A new user and the personal workspace created alongside them."""

    user: User
    workspace_id: uuid.UUID
    tokens: TokenPair


def _issue_tokens(user_id: uuid.UUID) -> TokenPair:
    access_token, access_claims = create_access_token(user_id)
    refresh_token, refresh_claims = create_refresh_token(user_id)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_claims=access_claims,
        refresh_claims=refresh_claims,
    )


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The name of the database constraint an IntegrityError violated.

    psycopg exposes it on the diagnostics of the underlying driver error.
    Returns ``None`` when the driver does not report one, so callers must treat
    "unknown" as "not the constraint I was expecting".
    """
    diagnostics = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return str(name) if name else None


class AuthService:
    """Registration, login, refresh and logout."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._workspaces = WorkspaceRepository(session)

    async def register(
        self, *, email: str, password: str, display_name: str, client_ip: str | None = None
    ) -> RegistrationResult:
        """Create a user plus their personal workspace.

        A user with no workspace can do nothing at all — every resource in the
        system is workspace-scoped — so the two are created in one transaction.
        A registration that half-succeeded would leave an account that cannot
        use the product.
        """
        if client_ip:
            await check_rate_limit("register:ip", client_ip, REGISTER_IP_LIMIT)

        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            raise ValidationError(str(exc)) from exc

        email = normalize_email(email)
        display_name = display_name.strip()
        if not display_name:
            raise ValidationError("Display name must not be empty.")

        # Checked for a clean error message, but the unique index is the real
        # guarantee — two simultaneous registrations both pass this check.
        if await self._users.email_exists(email):
            raise EmailAlreadyRegisteredError()

        try:
            user = await self._users.create(
                email=email, password_hash=password_hash, display_name=display_name
            )
            slug = await self._workspaces.generate_unique_slug(display_name)
            workspace = await self._workspaces.create(
                name=f"{display_name}'s workspace", slug=slug, owner_user_id=user.id
            )
            await self._workspaces.add_member(
                workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER
            )
        except IntegrityError as exc:
            await self._session.rollback()
            # Only claim "duplicate email" when that is actually the violated
            # constraint. Blanket-mapping every IntegrityError here hid a slug
            # length-check failure behind a completely misleading message, and
            # would hide the next one just as well.
            if _violated_constraint(exc) == "uq_users_email":
                raise EmailAlreadyRegisteredError() from exc
            logger.error(
                "registration failed on an unexpected constraint",
                extra={"constraint": _violated_constraint(exc)},
                exc_info=exc,
            )
            raise

        logger.info("user registered", extra={"user_id": str(user.id)})
        return RegistrationResult(
            user=user, workspace_id=workspace.id, tokens=_issue_tokens(user.id)
        )

    async def login(
        self, *, email: str, password: str, client_ip: str | None = None
    ) -> tuple[User, TokenPair]:
        """Verify credentials and issue tokens.

        Every failure path returns the same :class:`InvalidCredentialsError`,
        and the no-such-user path still performs a hash verification. Together
        those stop the endpoint from revealing which emails are registered —
        through the message, the status code, or response latency.
        """
        email = normalize_email(email)

        if client_ip:
            await check_rate_limit("login:ip", client_ip, LOGIN_IP_LIMIT)
        # Also per account, so spreading an attack across IPs does not evade
        # the limit. Keyed on the email as supplied, existing or not.
        await check_rate_limit("login:account", email, LOGIN_ACCOUNT_LIMIT)

        user = await self._users.get_by_email(email)

        if user is None:
            dummy_verify()
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        if user.status is not UserStatus.ACTIVE:
            # Suspended and deleted accounts fail identically to a wrong
            # password: saying "your account is suspended" confirms the address
            # is registered.
            raise InvalidCredentialsError()

        # The only moment the plaintext is available, so the only chance to
        # transparently upgrade a hash whose cost parameters are now outdated.
        if needs_rehash(user.password_hash):
            await self._users.update_password_hash(user, hash_password(password))

        await self._users.record_login(user)
        await reset_rate_limit("login:account", email)
        if client_ip:
            await reset_rate_limit("login:ip", client_ip)

        logger.info("user logged in", extra={"user_id": str(user.id)})
        return user, _issue_tokens(user.id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair.

        The old token is revoked as part of the exchange (rotation), so a
        stolen refresh token is usable at most once — and the legitimate user's
        next refresh fails, which is a detectable signal rather than a silent
        compromise.
        """
        claims = self._decode_refresh(refresh_token)

        if await is_refresh_token_revoked(claims):
            raise UnauthorizedError("This session has expired. Please sign in again.")

        user = await self._users.get_by_id(claims.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise UnauthorizedError("This session is no longer valid.")

        await revoke_refresh_token(claims)
        return _issue_tokens(user.id)

    async def logout(self, refresh_token: str | None) -> None:
        """Revoke a refresh token.

        Never raises: a logout that fails because the token was already
        expired or malformed has still achieved what the user asked for.
        """
        if not refresh_token:
            return
        try:
            claims = self._decode_refresh(refresh_token)
        except UnauthorizedError:
            return
        await revoke_refresh_token(claims)

    async def get_authenticated_user(self, access_token: str) -> User:
        """Resolve an access token to its user.

        Hits the database rather than trusting the token alone, so a suspended
        or deleted account loses access immediately instead of at token expiry.
        """
        try:
            claims = decode_token(access_token, TokenType.ACCESS)
        except Exception as exc:
            raise UnauthorizedError("Invalid or expired credentials.") from exc

        user = await self._users.get_by_id(claims.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            raise UnauthorizedError("Invalid or expired credentials.")
        return user

    @staticmethod
    def _decode_refresh(refresh_token: str) -> TokenClaims:
        try:
            return decode_token(refresh_token, TokenType.REFRESH)
        except Exception as exc:
            raise UnauthorizedError("This session has expired. Please sign in again.") from exc
