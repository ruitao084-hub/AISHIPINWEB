"""Shared SQLAlchemy metadata and constraint naming convention.

Why this exists in PHASE 1, before a single model does: Postgres auto-names
unnamed constraints, and Alembic cannot then emit a reliable ``DROP CONSTRAINT``
for them. Fixing the convention *after* migrations exist means rewriting every
constraint in a later migration. Setting it now costs nothing.

P2-T06 adds the ``DeclarativeBase`` bound to this metadata along with the UUID
primary key and timestamp mixins every entity needs (§9).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import MetaData

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: The single metadata object every table registers against. Alembic
#: autogeneration compares the live database to exactly this.
metadata: Final[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)
