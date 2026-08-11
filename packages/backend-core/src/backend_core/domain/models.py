"""ORM models for users, workspaces and membership (taskbook §10.1-§10.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend_core.db.base import BaseEntity
from backend_core.domain.enums import PlanCode, UserStatus, WorkspaceRole, WorkspaceStatus


def _pg_enum(enum_type: type[Any], name: str) -> Enum:
    """A Postgres ENUM storing member *values* rather than Python names.

    ``native_enum`` creates a real database type, so an invalid status cannot
    be written even by a hand-run SQL statement — §9 asks for status fields to
    be constrained by the database, not only by the application.
    """
    return Enum(
        enum_type,
        name=name,
        native_enum=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(BaseEntity):
    """A person with login credentials (§10.1).

    Not workspace-scoped: one user belongs to many workspaces through
    :class:`WorkspaceMember`.
    """

    __tablename__ = "users"

    # Stored lowercased and unique. Case-sensitive emails would let
    # "User@x.com" and "user@x.com" register as two accounts for one mailbox,
    # which is an account-takeover vector during password reset.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    # Argon2 output. Never the password, and never reversible.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    status: Mapped[UserStatus] = mapped_column(
        _pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        CheckConstraint("length(email) >= 3", name="email_min_length"),
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def __repr__(self) -> str:
        # Deliberately no email: reprs reach logs and tracebacks, and an email
        # is personal data (§62).
        return f"User(id={self.id!r}, status={self.status.value!r})"


class Workspace(BaseEntity):
    """A tenant boundary (§10.2).

    Every piece of content in the system hangs off exactly one workspace; it is
    the unit of isolation, billing and quota.
    """

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # RESTRICT, not CASCADE: deleting a user must not silently destroy a
    # workspace their colleagues are working in. Ownership is transferred
    # first, which forces the decision to be explicit.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    plan_code: Mapped[PlanCode] = mapped_column(
        _pg_enum(PlanCode, "plan_code"),
        nullable=False,
        default=PlanCode.FREE,
        server_default=PlanCode.FREE.value,
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        _pg_enum(WorkspaceStatus, "workspace_status"),
        nullable=False,
        default=WorkspaceStatus.ACTIVE,
        server_default=WorkspaceStatus.ACTIVE.value,
        index=True,
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_user_id], lazy="raise")
    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("slug = lower(slug)", name="slug_is_lowercase"),
        CheckConstraint("length(slug) >= 3", name="slug_min_length"),
    )

    @property
    def is_active(self) -> bool:
        return self.status is WorkspaceStatus.ACTIVE

    def __repr__(self) -> str:
        return f"Workspace(id={self.id!r}, slug={self.slug!r})"


class WorkspaceMember(BaseEntity):
    """A user's role within a workspace (§10.3).

    This row *is* the authorisation record: every permission check resolves to
    finding it and reading its role.
    """

    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        _pg_enum(WorkspaceRole, "workspace_role"),
        nullable=False,
        default=WorkspaceRole.VIEWER,
        server_default=WorkspaceRole.VIEWER.value,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")

    __table_args__ = (
        # §164 requires this at the database level. Enforcing it only in
        # application code loses to a race between two concurrent invites,
        # leaving one user with two roles and an ambiguous permission answer.
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        # Permission lookups are "this user, in this workspace" on every single
        # request, so the index leads with user_id.
        Index("ix_workspace_members_user_id_workspace_id", "user_id", "workspace_id"),
    )

    def __repr__(self) -> str:
        return (
            f"WorkspaceMember(workspace_id={self.workspace_id!r}, "
            f"user_id={self.user_id!r}, role={self.role.value!r})"
        )
