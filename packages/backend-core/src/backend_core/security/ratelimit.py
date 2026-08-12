"""Redis-backed rate limiting (taskbook §39, §123).

§123 wants limits on login, presign, analyze, generate and render, keyed by IP,
user or workspace. This is the shared mechanism; each phase adds its own limits
as those endpoints arrive.

A fixed window is used rather than a sliding log: it costs one ``INCR`` plus one
``EXPIRE`` instead of storing every hit, and its known weakness — up to 2x the
limit across a window boundary — does not matter for brute-force defence, where
the goal is to make thousands of attempts infeasible, not to police the
difference between 5 and 10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend_core.errors import AppError, ErrorCode

_KEY_PREFIX: Final[str] = "ratelimit:"


class RateLimitExceededError(AppError):
    """Too many attempts. Maps to HTTP 429."""

    code = ErrorCode.PROVIDER_RATE_LIMITED
    http_status = 429
    retryable = True
    default_message = "Too many attempts. Please try again later."


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A limit of ``max_attempts`` per ``window_seconds``."""

    max_attempts: int
    window_seconds: int


#: Login. Deliberately strict: this is the endpoint an attacker sprays.
LOGIN_IP_LIMIT: Final[RateLimit] = RateLimit(max_attempts=10, window_seconds=300)
#: Per account, so distributing an attack across IPs does not evade the limit.
LOGIN_ACCOUNT_LIMIT: Final[RateLimit] = RateLimit(max_attempts=5, window_seconds=900)
#: Registration, to slow bulk account creation.
REGISTER_IP_LIMIT: Final[RateLimit] = RateLimit(max_attempts=5, window_seconds=3600)


async def check_rate_limit(scope: str, identifier: str, limit: RateLimit) -> int:
    """Record an attempt and raise if the limit is exceeded.

    Returns the attempt count within the current window.

    Fails **open** if Redis is unavailable: a rate limiter that cannot reach its
    store should not take the whole login endpoint down with it. The tradeoff is
    deliberate and worth stating — during a Redis outage brute-force protection
    is reduced to whatever the edge provides.
    """
    from backend_core.cache import get_redis

    key = f"{_KEY_PREFIX}{scope}:{identifier}"
    try:
        redis = get_redis()
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, limit.window_seconds)
    except Exception:
        return 0

    if attempts > limit.max_attempts:
        raise RateLimitExceededError(details={"retry_after_seconds": limit.window_seconds})
    return int(attempts)


async def reset_rate_limit(scope: str, identifier: str) -> None:
    """Clear a counter.

    Called after a successful login so a user who mistyped their password twice
    is not locked out by their own earlier failures.
    """
    from backend_core.cache import get_redis

    try:
        await get_redis().delete(f"{_KEY_PREFIX}{scope}:{identifier}")
    except Exception:
        return
