"""Workspace and membership service (taskbook §40)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import Permission, WorkspaceRole
from backend_core.domain.models import User, Workspace, WorkspaceMember
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.repositories.users import UserRepository
from backend_core.repositories.workspaces import WorkspaceRepository
from backend_core.security import ensure_membership, ensure_permission

logger = get_logger(__name__)


class LastOwnerError(AppError):
    """Refuses to leave a workspace without an owner."""

    code = ErrorCode.PROJECT_INVALID_STATE
    http_status = 409
    default_message = (
        "A workspace must always have at least one owner. Promote another member first."
    )


class AlreadyMemberError(AppError):
    """That user already belongs to this workspace."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 409
    default_message = "That user is already a member of this workspace."


@dataclass(frozen=True, slots=True)
class WorkspaceWithRole:
    """A workspace together with the caller's role in it."""

    workspace: Workspace
    role: WorkspaceRole


class WorkspaceService:
    """Workspace lifecycle and membership management."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._workspaces = WorkspaceRepository(session)
        self._users = UserRepository(session)

    # -- workspaces --------------------------------------------------------

    async def create(self, *, name: str, owner: User) -> Workspace:
        """Create a workspace with its creator as OWNER."""
        name = name.strip()
        if not name:
            raise ValidationError("Workspace name must not be empty.")

        slug = await self._workspaces.generate_unique_slug(name)
        workspace = await self._workspaces.create(name=name, slug=slug, owner_user_id=owner.id)
        await self._workspaces.add_member(
            workspace_id=workspace.id, user_id=owner.id, role=WorkspaceRole.OWNER
        )
        logger.info(
            "workspace created",
            extra={"workspace_id": str(workspace.id), "user_id": str(owner.id)},
        )
        return workspace

    async def list_for_user(self, user: User) -> list[WorkspaceWithRole]:
        return [
            WorkspaceWithRole(workspace=workspace, role=role)
            for workspace, role in await self._workspaces.list_for_user(user.id)
        ]

    async def get_for_user(self, workspace_id: uuid.UUID, user: User) -> WorkspaceWithRole:
        """Fetch a workspace the caller belongs to.

        A non-member gets 404, never 403 — a 403 would confirm the id is real
        and let workspace ids be probed across tenants.
        """
        role = await self._workspaces.get_role(workspace_id, user.id)
        role = ensure_membership(role, workspace_id)

        workspace = await self._workspaces.get_for_user(workspace_id, user.id)
        if workspace is None:
            raise NotFoundError("Workspace not found.")
        return WorkspaceWithRole(workspace=workspace, role=role)

    async def rename(self, *, workspace_id: uuid.UUID, user: User, name: str) -> Workspace:
        current = await self.get_for_user(workspace_id, user)
        ensure_permission(current.role, Permission.WORKSPACE_UPDATE)

        name = name.strip()
        if not name:
            raise ValidationError("Workspace name must not be empty.")

        # The slug is deliberately left alone: it appears in URLs, and silently
        # changing it on rename breaks every link anyone has shared.
        current.workspace.name = name
        await self._session.flush()
        return current.workspace

    # -- membership --------------------------------------------------------

    async def list_members(self, *, workspace_id: uuid.UUID, user: User) -> list[WorkspaceMember]:
        current = await self.get_for_user(workspace_id, user)
        ensure_permission(current.role, Permission.MEMBER_READ)
        return await self._workspaces.list_members(workspace_id)

    async def add_member(
        self,
        *,
        workspace_id: uuid.UUID,
        user: User,
        member_email: str,
        role: WorkspaceRole,
    ) -> WorkspaceMember:
        """Add an existing user to a workspace."""
        current = await self.get_for_user(workspace_id, user)
        ensure_permission(current.role, Permission.MEMBER_INVITE)

        # Granting OWNER is a role change, not an invite: an ADMIN who could
        # invite an owner could invite an account they control and escalate.
        if role is WorkspaceRole.OWNER:
            ensure_permission(current.role, Permission.MEMBER_UPDATE_ROLE)

        invitee = await self._users.get_by_email(member_email)
        if invitee is None:
            # Same response whether or not the address has an account, so
            # membership management cannot be used to enumerate users.
            raise NotFoundError("No user with that email address.")

        try:
            member = await self._workspaces.add_member(
                workspace_id=workspace_id, user_id=invitee.id, role=role
            )
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyMemberError() from exc

        logger.info(
            "member added",
            extra={"workspace_id": str(workspace_id), "role": role.value},
        )
        return member

    async def update_member_role(
        self,
        *,
        workspace_id: uuid.UUID,
        user: User,
        member_id: uuid.UUID,
        role: WorkspaceRole,
    ) -> WorkspaceMember:
        current = await self.get_for_user(workspace_id, user)
        ensure_permission(current.role, Permission.MEMBER_UPDATE_ROLE)

        member = await self._workspaces.get_member_by_id(workspace_id, member_id)
        if member is None:
            raise NotFoundError("Member not found.")

        if member.role is WorkspaceRole.OWNER and role is not WorkspaceRole.OWNER:
            await self._ensure_not_last_owner(workspace_id)

        member.role = role
        await self._session.flush()
        logger.info(
            "member role changed",
            extra={"workspace_id": str(workspace_id), "role": role.value},
        )
        return member

    async def remove_member(
        self, *, workspace_id: uuid.UUID, user: User, member_id: uuid.UUID
    ) -> None:
        current = await self.get_for_user(workspace_id, user)

        member = await self._workspaces.get_member_by_id(workspace_id, member_id)
        if member is None:
            raise NotFoundError("Member not found.")

        # Leaving is always allowed; removing someone else needs permission.
        if member.user_id != user.id:
            ensure_permission(current.role, Permission.MEMBER_REMOVE)

        if member.role is WorkspaceRole.OWNER:
            await self._ensure_not_last_owner(workspace_id)

        await self._workspaces.remove_member(member)
        logger.info("member removed", extra={"workspace_id": str(workspace_id)})

    async def _ensure_not_last_owner(self, workspace_id: uuid.UUID) -> None:
        if await self._workspaces.count_owners(workspace_id) <= 1:
            raise LastOwnerError()
