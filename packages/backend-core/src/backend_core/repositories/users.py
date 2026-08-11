"""User data access."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.models import User


def normalize_email(email: str) -> str:
    """Canonical form used for both storage and lookup.

    Lowercasing is what makes the unique index meaningful: without it
    ``User@x.com`` and ``user@x.com`` are two accounts for one mailbox, which
    is an account-takeover path during password reset.
    """
    return email.strip().lower()


class UserRepository:
    """Reads and writes for :class:`~backend_core.domain.models.User`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == normalize_email(email))
        )
        return result.scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        result = await self._session.execute(
            select(User.id).where(User.email == normalize_email(email))
        )
        return result.first() is not None

    async def create(self, *, email: str, password_hash: str, display_name: str) -> User:
        """Insert a user. Does not commit — the caller owns the transaction."""
        user = User(
            email=normalize_email(email),
            password_hash=password_hash,
            display_name=display_name.strip(),
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def record_login(self, user: User) -> None:
        user.last_login_at = datetime.now(tz=UTC)
        await self._session.flush()

    async def update_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self._session.flush()
