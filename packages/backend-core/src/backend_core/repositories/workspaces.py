"""Workspace and membership data access.

Every method here is workspace-scoped by construction (§9). There is
deliberately no "get workspace by id" that skips the membership join: the only
way to reach a workspace is through a membership the caller actually holds, so
a missing check cannot silently expose another tenant.
"""

from __future__ import annotations

import re
import uuid
from secrets import token_hex

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import WorkspaceRole
from backend_core.domain.models import Workspace, WorkspaceMember

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

#: Mirrors the `length(slug) >= 3` check constraint on `workspaces`. A slug
#: shorter than this is rejected by the database, so it must never be produced.
MIN_SLUG_LENGTH = 3
MAX_SLUG_LENGTH = 48


def slugify(name: str) -> str:
    """Turn a display name into a URL-safe slug fragment.

    Two cases that are easy to miss and both break registration outright:

    * A name that is entirely non-ASCII — Chinese is a first-class case here —
      strips to the empty string.
    * A very short name ("A", "李") yields a slug below the database's
      three-character minimum.

    Both are padded rather than rejected: the user picked their display name,
    and it is not their problem that it transliterates poorly.
    """
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")[:MAX_SLUG_LENGTH]
    if not slug:
        return "workspace"
    if len(slug) < MIN_SLUG_LENGTH:
        return f"ws-{slug}"
    return slug


class WorkspaceRepository:
    """Reads and writes for workspaces and their membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- workspaces --------------------------------------------------------

    async def create(self, *, name: str, slug: str, owner_user_id: uuid.UUID) -> Workspace:
        """Insert a workspace. Does not commit."""
        workspace = Workspace(name=name.strip(), slug=slug, owner_user_id=owner_user_id)
        self._session.add(workspace)
        await self._session.flush()
        return workspace

    async def generate_unique_slug(self, name: str) -> str:
        """A slug not currently taken.

        Suffixes with random hex rather than an incrementing counter: a counter
        leaks how many workspaces share a name, and races between two concurrent
        creations. The unique index remains the real guarantee.
        """
        base = slugify(name)
        if not await self._slug_exists(base):
            return base
        for _ in range(5):
            candidate = f"{base}-{token_hex(3)}"
            if not await self._slug_exists(candidate):
                return candidate
        return f"{base}-{uuid.uuid4().hex[:12]}"

    async def _slug_exists(self, slug: str) -> bool:
        result = await self._session.execute(select(Workspace.id).where(Workspace.slug == slug))
        return result.first() is not None

    async def get_for_user(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Workspace | None:
        """The workspace, only if this user is a member of it.

        Returning ``None`` for both "does not exist" and "not yours" is
        deliberate: the caller turns either into a 404, so workspace ids cannot
        be probed for existence.
        """
        result = await self._session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[tuple[Workspace, WorkspaceRole]]:
        """Every workspace this user belongs to, with their role in each."""
        result = await self._session.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.created_at)
        )
        return [(row[0], row[1]) for row in result.all()]

    # -- membership --------------------------------------------------------

    async def add_member(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID, role: WorkspaceRole
    ) -> WorkspaceMember:
        """Insert a membership row. Does not commit.

        A duplicate raises ``IntegrityError`` from the unique constraint; the
        service layer translates it. Relying on the constraint rather than a
        prior SELECT is what makes concurrent invites safe (§164).
        """
        member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
        self._session.add(member)
        await self._session.flush()
        return member

    async def get_role(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceRole | None:
        """This user's role in this workspace, or ``None`` if not a member.

        The single most-called authorisation query in the system — the
        ``(user_id, workspace_id)`` index exists for exactly this.
        """
        result = await self._session.execute(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_member(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> WorkspaceMember | None:
        result = await self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_member_by_id(
        self, workspace_id: uuid.UUID, member_id: uuid.UUID
    ) -> WorkspaceMember | None:
        """Look up by membership id, still scoped to the workspace.

        The ``workspace_id`` predicate is not redundant: without it, a member id
        from another workspace would resolve, and the caller's permission check
        would have been performed against the wrong tenant.
        """
        result = await self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.id == member_id,
                WorkspaceMember.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_members(self, workspace_id: uuid.UUID) -> list[WorkspaceMember]:
        result = await self._session.execute(
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(WorkspaceMember.created_at)
        )
        return list(result.scalars().all())

    async def count_owners(self, workspace_id: uuid.UUID) -> int:
        """How many owners a workspace has.

        Used to refuse the last owner's removal or demotion — a workspace with
        no owner cannot be billed, deleted or have its membership managed.
        """
        result = await self._session.execute(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == WorkspaceRole.OWNER,
            )
        )
        return len(result.all())

    async def remove_member(self, member: WorkspaceMember) -> None:
        await self._session.delete(member)
        await self._session.flush()
