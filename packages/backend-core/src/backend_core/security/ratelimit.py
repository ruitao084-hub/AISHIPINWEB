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


# ---------------------------------------------------------------------------
# §123's limits on the expensive endpoints (P16-T02)
# ---------------------------------------------------------------------------
#
# Keyed by workspace rather than by IP or user. The cost these limits protect
# against is the workspace's — a provider bill and a queue full of one tenant's
# work — and a per-user limit would be evaded by inviting a second member.
#
# The numbers are generous on purpose. A limit that a normal working session
# hits is a bug report, not a defence; these exist to stop a script, and a
# script does thousands, not dozens.

#: Product analysis: a vision call per invocation, over several images.
ANALYZE_WORKSPACE_LIMIT: Final[RateLimit] = RateLimit(max_attempts=60, window_seconds=3600)
#: Shot generation. The most expensive thing this platform does.
GENERATE_WORKSPACE_LIMIT: Final[RateLimit] = RateLimit(max_attempts=200, window_seconds=3600)
#: Composition. Cheap per call, but each one pins a CPU for minutes (§25).
RENDER_WORKSPACE_LIMIT: Final[RateLimit] = RateLimit(max_attempts=60, window_seconds=3600)
#: Presign. Cheap, but an unbounded stream of them is how storage fills up.
PRESIGN_WORKSPACE_LIMIT: Final[RateLimit] = RateLimit(max_attempts=600, window_seconds=3600)
#: LLM calls behind creative planning and scripting.
CREATIVE_WORKSPACE_LIMIT: Final[RateLimit] = RateLimit(max_attempts=120, window_seconds=3600)


#: Named limits, so a route declares `"generate"` rather than importing a
#: constant and the API's dependency has one table to look them up in.
WORKSPACE_LIMITS: Final[dict[str, RateLimit]] = {
    "analyze": ANALYZE_WORKSPACE_LIMIT,
    "generate": GENERATE_WORKSPACE_LIMIT,
    "render": RENDER_WORKSPACE_LIMIT,
    "presign": PRESIGN_WORKSPACE_LIMIT,
    "creative": CREATIVE_WORKSPACE_LIMIT,
}


async def check_workspace_limit(scope: str, workspace_id: str) -> int:
    """Apply one of §123's per-workspace limits.

    Raises `KeyError` for an unknown scope rather than falling back to a
    permissive default: a typo in a route decorator should fail loudly at the
    first request, not silently remove the limit.
    """
    return await check_rate_limit(f"ws:{scope}", workspace_id, WORKSPACE_LIMITS[scope])
