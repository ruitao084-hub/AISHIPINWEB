"""Database access (taskbook §4.4, P1-T04).

The API is asynchronous while Alembic and the Celery workers are synchronous.
Both engines address the same database through psycopg3, which SQLAlchemy 2
drives in either mode from an identical ``postgresql+psycopg://`` URL — so
there is one canonical ``DATABASE_URL`` and no second connection string to keep
in sync.

ORM base classes and the shared column mixins land in P2-T06.
"""

from backend_core.db.engine import (
    dispose_engines,
    get_async_engine,
    get_async_session,
    get_async_sessionmaker,
    get_sync_engine,
    get_sync_session,
    get_sync_sessionmaker,
    ping_database,
    reset_engine_cache,
)

__all__ = [
    "dispose_engines",
    "get_async_engine",
    "get_async_session",
    "get_async_sessionmaker",
    "get_sync_engine",
    "get_sync_session",
    "get_sync_sessionmaker",
    "ping_database",
    "reset_engine_cache",
]
