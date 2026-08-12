"""Workspace and membership endpoints (taskbook §42 Workspace, §40)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, EmailStr, Field

from aipvs_api.dependencies import CurrentUser, SessionDep
from backend_core.domain.enums import WorkspaceRole, permissions_for
from backend_core.domain.models import Workspace, WorkspaceMember
from backend_core.services.workspaces import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan_code: str
    status: str
    created_at: datetime
    role: str = Field(description="The calling user's role in this workspace.")
    permissions: list[str] = Field(
        description=(
            "What the caller may do here. The UI uses this to hide actions; "
            "the server enforces it regardless (§40)."
        )
    )

    @classmethod
    def of(cls, workspace: Workspace, role: WorkspaceRole) -> WorkspaceResponse:
        return cls(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            plan_code=workspace.plan_code.value,
            status=workspace.status.value,
            created_at=workspace.created_at,
            role=role.value,
            permissions=sorted(p.value for p in permissions_for(role)),
        )


class MemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: str
    created_at: datetime

    @classmethod
    def of(cls, member: WorkspaceMember) -> MemberResponse:
        return cls(
            id=member.id,
            user_id=member.user_id,
            role=member.role.value,
            created_at=member.created_at,
        )


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class UpdateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.VIEWER


class UpdateMemberRoleRequest(BaseModel):
    role: WorkspaceRole


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
async def create_workspace(
    payload: CreateWorkspaceRequest, user: CurrentUser, session: SessionDep
) -> WorkspaceResponse:
    """Create a workspace. The caller becomes its OWNER."""
    workspace = await WorkspaceService(session).create(name=payload.name, owner=user)
    return WorkspaceResponse.of(workspace, WorkspaceRole.OWNER)


@router.get("", response_model=list[WorkspaceResponse], summary="List your workspaces")
async def list_workspaces(user: CurrentUser, session: SessionDep) -> list[WorkspaceResponse]:
    """Every workspace the caller belongs to. Never any others."""
    return [
        WorkspaceResponse.of(entry.workspace, entry.role)
        for entry in await WorkspaceService(session).list_for_user(user)
    ]


@router.get("/{workspace_id}", response_model=WorkspaceResponse, summary="Get a workspace")
async def get_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> WorkspaceResponse:
    """Fetch one workspace.

    Returns 404 — not 403 — when the caller is not a member, so workspace ids
    cannot be probed for existence across tenants.
    """
    entry = await WorkspaceService(session).get_for_user(workspace_id, user)
    return WorkspaceResponse.of(entry.workspace, entry.role)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse, summary="Rename a workspace")
async def update_workspace(
    workspace_id: uuid.UUID,
    payload: UpdateWorkspaceRequest,
    user: CurrentUser,
    session: SessionDep,
) -> WorkspaceResponse:
    """Rename a workspace. Requires `workspace:update`."""
    service = WorkspaceService(session)
    workspace = await service.rename(workspace_id=workspace_id, user=user, name=payload.name)
    entry = await service.get_for_user(workspace_id, user)
    return WorkspaceResponse.of(workspace, entry.role)


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberResponse],
    summary="List members",
)
async def list_members(
    workspace_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[MemberResponse]:
    members = await WorkspaceService(session).list_members(workspace_id=workspace_id, user=user)
    return [MemberResponse.of(member) for member in members]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member",
)
async def add_member(
    workspace_id: uuid.UUID,
    payload: AddMemberRequest,
    user: CurrentUser,
    session: SessionDep,
) -> MemberResponse:
    """Add an existing user. Requires `member:invite`.

    Granting OWNER additionally requires `member:update_role`, so an admin
    cannot escalate by inviting an account they control as an owner.
    """
    member = await WorkspaceService(session).add_member(
        workspace_id=workspace_id,
        user=user,
        member_email=payload.email,
        role=payload.role,
    )
    return MemberResponse.of(member)


@router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=MemberResponse,
    summary="Change a member's role",
)
async def update_member_role(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: UpdateMemberRoleRequest,
    user: CurrentUser,
    session: SessionDep,
) -> MemberResponse:
    """Change a role. Requires `member:update_role` (OWNER only).

    Demoting the last owner is refused: a workspace with no owner cannot be
    billed, deleted, or have its membership managed.
    """
    member = await WorkspaceService(session).update_member_role(
        workspace_id=workspace_id, user=user, member_id=member_id, role=payload.role
    )
    return MemberResponse.of(member)


@router.delete(
    "/{workspace_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
)
async def remove_member(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    """Remove a member, or leave the workspace yourself.

    Leaving needs no permission; removing someone else requires
    `member:remove`. Removing the last owner is refused either way.
    """
    await WorkspaceService(session).remove_member(
        workspace_id=workspace_id, user=user, member_id=member_id
    )
