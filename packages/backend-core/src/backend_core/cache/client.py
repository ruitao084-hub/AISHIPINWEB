"""Shared async Redis client (P1-T06)."""

from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

from redis.asyncio import Redis

from backend_core.config import get_settings

# An asyncio Redis connection pool binds to the event loop that created it;
# reusing one under a different loop raises "Event loop is closed". A single
# process-wide client is therefore wrong for anything that runs more than one
# loop — a Celery task calling `asyncio.run()` per job, or a test suite with a
# loop per test. Caching per loop keeps one pool per loop, which is what the
# API (a single long-lived loop) wanted anyway.
#
# Weak keys let a finished loop's entry disappear with the loop itself.
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, Redis] = WeakKeyDictionary()


def _build_client() -> Redis:
    """Construct a client from settings.

    redis-py pools lazily, so this opens no socket — a process that never
    touches Redis never connects.

    ``decode_responses=True`` keeps callers working in ``str``; the few places
    needing raw bytes (media checksums, packed payloads) build their own client
    rather than making every other call site handle ``bytes``.
    """
    settings = get_settings()
    return Redis.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        # Detect half-open connections after an idle period behind a NAT/LB.
        health_check_interval=30,
        decode_responses=True,
    )


def get_redis() -> Redis:
    """Return the Redis client belonging to the running event loop.

    Must be called from async context; every caller is already async.
    """
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = _build_client()
        _clients[loop] = client
    return client


async def ping_redis() -> bool:
    """Return whether Redis answers PING. Used by the readiness probe (§70)."""
    try:
        await get_redis().ping()
    except Exception:
        return False
    return True


async def close_redis() -> None:
    """Close the running loop's pool. Call on shutdown."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    client = _clients.pop(loop, None)
    if client is not None:
        await client.aclose()


def reset_redis_cache() -> None:
    """Forget all cached clients without closing them. Tests only."""
    _clients.clear()
