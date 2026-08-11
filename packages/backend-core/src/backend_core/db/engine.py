"""SQLAlchemy 2 engines, connection pools and session factories (P1-T04)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from weakref import WeakKeyDictionary

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from backend_core.config import get_settings

# An async engine's connection pool binds to the event loop that created it, so
# a single process-wide engine breaks in any process that runs more than one
# loop — a Celery task calling `asyncio.run()` per job, or a test suite with a
# loop per test. Caching per loop yields one engine per loop, which for the API
# (a single long-lived loop) is exactly one engine.
#
# Weak keys let a finished loop's entry disappear along with the loop.
_async_engines: WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncEngine] = WeakKeyDictionary()
_async_sessionmakers: WeakKeyDictionary[
    asyncio.AbstractEventLoop, async_sessionmaker[AsyncSession]
] = WeakKeyDictionary()


def _build_async_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        # Verify a pooled connection is still alive before handing it out.
        # Costs one round trip; avoids surfacing the database's idle timeout as
        # a random request failure.
        pool_pre_ping=True,
    )


def get_async_engine() -> AsyncEngine:
    """Engine for the API. One pool per event loop, never one per request."""
    loop = asyncio.get_running_loop()
    engine = _async_engines.get(loop)
    if engine is None:
        engine = _build_async_engine()
        _async_engines[loop] = engine
    return engine


def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Session factory for the API, bound to the running loop's engine."""
    loop = asyncio.get_running_loop()
    factory = _async_sessionmakers.get(loop)
    if factory is None:
        factory = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            # Attributes stay usable after commit, so a handler can serialise
            # an object it just wrote without a surprise lazy reload.
            expire_on_commit=False,
            autoflush=False,
        )
        _async_sessionmakers[loop] = factory
    return factory


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    """Engine for Alembic and the Celery workers.

    Synchronous pools have no loop affinity, so one per process is correct.
    The pool is deliberately smaller than the API's: workers hold a connection
    for the length of a job rather than a request, and a worker fleet that each
    grabbed an API-sized pool would exhaust Postgres' connection limit.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=max(2, settings.database_pool_size // 2),
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sync_sessionmaker() -> sessionmaker[Session]:
    """Session factory for workers and migrations."""
    return sessionmaker(
        bind=get_sync_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def get_async_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that commits on success and rolls back on failure.

    Transaction ownership sits here rather than at call sites so a handler
    cannot leave a transaction half-open on an error path (§0.1 rule 14).
    """
    async with get_async_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def get_sync_session() -> Iterator[Session]:
    """Synchronous counterpart of :func:`get_async_session`."""
    with get_sync_sessionmaker()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def ping_database() -> bool:
    """Return whether the database answers a trivial query.

    Used by the readiness probe (§70). Swallows the exception deliberately: the
    caller needs a yes/no to decide whether to serve traffic, not a traceback.
    """
    try:
        async with get_async_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engines() -> None:
    """Close pooled connections for the running loop. Call on shutdown."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        _async_sessionmakers.pop(loop, None)
        engine = _async_engines.pop(loop, None)
        if engine is not None:
            await engine.dispose()

    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose()


def reset_engine_cache() -> None:
    """Forget cached engines so the next call rebuilds them. Tests only."""
    _async_engines.clear()
    _async_sessionmakers.clear()
    get_sync_engine.cache_clear()
    get_sync_sessionmaker.cache_clear()
