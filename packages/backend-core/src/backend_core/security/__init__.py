"""Authentication, authorisation and safe I/O (§39-§40, §61).

Password hashing, token issue and verification, RBAC checks and rate limiting.
The SSRF-safe fetcher every user-supplied URL must pass through lands in
PHASE 16 (P16-T04), alongside the first endpoint that accepts one.
"""

from backend_core.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from backend_core.security.ratelimit import (
    LOGIN_ACCOUNT_LIMIT,
    LOGIN_IP_LIMIT,
    REGISTER_IP_LIMIT,
    RateLimit,
    RateLimitExceededError,
    check_rate_limit,
    reset_rate_limit,
)
from backend_core.security.rbac import can, ensure_membership, ensure_permission
from backend_core.security.tokens import (
    TokenClaims,
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_refresh_token_revoked,
    revoke_refresh_token,
)

__all__ = [
    "LOGIN_ACCOUNT_LIMIT",
    "LOGIN_IP_LIMIT",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "REGISTER_IP_LIMIT",
    "PasswordPolicyError",
    "RateLimit",
    "RateLimitExceededError",
    "TokenClaims",
    "TokenError",
    "TokenType",
    "can",
    "check_rate_limit",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "dummy_verify",
    "ensure_membership",
    "ensure_permission",
    "hash_password",
    "is_refresh_token_revoked",
    "needs_rehash",
    "reset_rate_limit",
    "revoke_refresh_token",
    "validate_password_policy",
    "verify_password",
]
