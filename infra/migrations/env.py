"""Alembic migration environment (P1-T05).

The database URL comes from application settings rather than ``alembic.ini`` so
there is one source of truth and no credentials in a tracked file (§7, §138).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import backend_core.domain  # noqa: F401 - registers models on the metadata
from backend_core.config import get_settings
from backend_core.db.metadata import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogeneration diffs the live database against exactly this. Importing
# `backend_core.domain` above is what registers the models — without it Alembic
# would cheerfully generate a migration dropping every table it cannot see.
target_metadata = metadata

config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    Used to review a migration before it touches a production database (§73).
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch column type and default drift, not just added/dropped
            # tables — otherwise a widened varchar silently diverges between
            # the models and the database.
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
