"""Access and refresh token issue and verification (taskbook §39).

Design is recorded in ADR-0006. The essentials:

* **Access tokens are short-lived JWTs** verified by signature alone, so the
  hot path costs no database round trip.
* **Refresh tokens are also JWTs but carry a ``jti``**, which is what makes
  revocation possible: the id is checked against a Redis denylist on use.
  Without a jti, "log out everywhere" is unimplementable for a stateless token.
* **The two token types are not interchangeable.** Each carries a ``type``
  claim that is verified, so a refresh token cannot be presented as an access
  token to gain a much longer effective session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import jwt

from backend_core.config import get_settings

_ISSUER: Final[str] = "aipvs"


class TokenType(StrEnum):
    """Which kind of token a JWT is. Verified on every use."""

    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """A token is missing, malformed, expired or of the wrong type."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The verified contents of a token."""

    user_id: uuid.UUID
    token_type: TokenType
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _secret() -> str:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    if not secret:
        # Never fall back to a generated or default key: tokens would verify
        # locally and silently fail across restarts and replicas.
        raise TokenError("JWT_SECRET is not configured")
    return secret


def _encode(user_id: uuid.UUID, token_type: TokenType, ttl_seconds: int) -> tuple[str, TokenClaims]:
    settings = get_settings()
    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    jti = uuid.uuid4()

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type.value,
        "jti": str(jti),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": _ISSUER,
    }
    token = jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)
    claims = TokenClaims(
        user_id=user_id,
        token_type=token_type,
        jti=jti,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return token, claims


def create_access_token(user_id: uuid.UUID) -> tuple[str, TokenClaims]:
    """Issue a short-lived access token."""
    return _encode(user_id, TokenType.ACCESS, get_settings().jwt_access_token_ttl)


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, TokenClaims]:
    """Issue a long-lived refresh token."""
    return _encode(user_id, TokenType.REFRESH, get_settings().jwt_refresh_token_ttl)


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    """Verify a token and return its claims.

    Raises :class:`TokenError` for anything wrong — bad signature, expiry,
    wrong issuer, wrong type, malformed subject. The caller gets one failure
    mode, so it cannot leak *why* a token was rejected.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            _secret(),
            # A list, not the raw string: passing a single algorithm still
            # lets a caller-supplied `alg` header be honoured in some
            # libraries, which is the classic JWT confusion attack.
            algorithms=[settings.jwt_algorithm],
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "type", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc

    if payload.get("type") != expected_type.value:
        raise TokenError("Token is not of the expected type")

    try:
        user_id = uuid.UUID(str(payload["sub"]))
        jti = uuid.UUID(str(payload["jti"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("Token claims are malformed") from exc

    return TokenClaims(
        user_id=user_id,
        token_type=expected_type,
        jti=jti,
        issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
    )


# --- Revocation ------------------------------------------------------------
#
# A stateless JWT cannot be withdrawn, so logout records the refresh token's
# jti in Redis until its natural expiry. The key expires on its own, meaning
# the denylist stays bounded by the refresh TTL rather than growing forever.

_REVOKED_KEY_PREFIX: Final[str] = "auth:revoked_refresh:"


def _revocation_key(jti: uuid.UUID) -> str:
    return f"{_REVOKED_KEY_PREFIX}{jti}"


async def revoke_refresh_token(claims: TokenClaims) -> None:
    """Deny future use of this refresh token."""
    from backend_core.cache import get_redis

    ttl = max(1, int((claims.expires_at - _now()).total_seconds()))
    await get_redis().set(_revocation_key(claims.jti), "1", ex=ttl)


async def is_refresh_token_revoked(claims: TokenClaims) -> bool:
    """Whether this refresh token has been revoked."""
    from backend_core.cache import get_redis

    return await get_redis().exists(_revocation_key(claims.jti)) > 0
