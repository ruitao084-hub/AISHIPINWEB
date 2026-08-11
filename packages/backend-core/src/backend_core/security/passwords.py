"""Password hashing and policy (taskbook §39).

Argon2id is chosen over bcrypt: it is the current OWASP recommendation, it
resists GPU and ASIC attack through memory-hardness rather than iteration count
alone, and it has no 72-byte silent truncation limit — bcrypt ignores anything
past 72 bytes, which turns a long passphrase into a much weaker secret without
telling anyone.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

# OWASP's second recommended Argon2id profile: 19 MiB, 2 iterations, 1 lane.
# Memory cost is the meaningful defence; raising time_cost alone buys far less.
_HASHER: Final[PasswordHasher] = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

MIN_PASSWORD_LENGTH: Final[int] = 12
# Argon2 has no truncation limit, but an unbounded password is a cheap DoS:
# hashing a 10 MB string costs real CPU, and the request is unauthenticated.
MAX_PASSWORD_LENGTH: Final[int] = 256


class PasswordPolicyError(ValueError):
    """The password does not meet policy. The message is safe to show a user."""


def normalize_password(password: str) -> str:
    """Apply Unicode NFKC normalisation.

    Without it, two byte sequences that render identically hash differently, so
    a user who set their password on one keyboard layout or platform can be
    unable to log in from another.
    """
    return unicodedata.normalize("NFKC", password)


def validate_password_policy(password: str) -> None:
    """Raise :class:`PasswordPolicyError` if the password is unacceptable.

    Length is the requirement that actually correlates with strength. Composition
    rules ("one uppercase, one digit, one symbol") are deliberately not enforced:
    they push people toward predictable patterns like ``Password1!`` while
    blocking strong passphrases, and both NIST SP 800-63B and OWASP now advise
    against them.
    """
    normalized = normalize_password(password)

    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if not normalized.strip():
        raise PasswordPolicyError("Password must not be only whitespace.")


def hash_password(password: str) -> str:
    """Validate policy, then hash. Returns an encoded Argon2 string.

    The returned value carries its own parameters and salt, so verification
    needs nothing else stored alongside it.
    """
    validate_password_policy(password)
    return _HASHER.hash(normalize_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored hash.

    Returns ``False`` rather than raising for every failure mode — including a
    malformed stored hash — so a caller cannot accidentally distinguish "wrong
    password" from "corrupt record" and turn it into an oracle.
    """
    try:
        return _HASHER.verify(password_hash, normalize_password(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash uses outdated parameters.

    Called after a successful login: it is the only moment the plaintext is
    available, so it is the only chance to transparently upgrade the hash when
    the cost parameters are raised later.
    """
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn the cost of a verification against a throwaway hash.

    Called on the login path when no user matches, so that a failed login takes
    the same time whether or not the account exists. Without it, response
    latency alone enumerates registered emails.
    """
    try:
        _HASHER.verify(_DUMMY_HASH, "invalid-password-placeholder")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return


#: Computed once at import: a real Argon2 hash to verify against on the
#: no-such-user path.
_DUMMY_HASH: Final[str] = _HASHER.hash("dummy-password-for-timing-equalisation")
