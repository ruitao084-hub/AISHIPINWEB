"""Domain enumerations.

These are database constraints as much as Python types (§9: "status fields use
an Enum or a database constraint"). Every value here is persisted, so renaming
one is a migration, not a refactor.
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    """Lifecycle of a user account (§10.1)."""

    ACTIVE = "ACTIVE"
    #: Set by an administrator. Blocks login but preserves all data.
    SUSPENDED = "SUSPENDED"
    #: Soft-deleted. Retained so audit history and authored content survive.
    DELETED = "DELETED"


class WorkspaceStatus(StrEnum):
    """Lifecycle of a workspace (§10.2)."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class PlanCode(StrEnum):
    """Subscription tier (§125).

    Recorded from the start even though billing lands in PHASE 18, because a
    workspace created before plans exist still needs a defensible default.
    """

    FREE = "FREE"
    PRO = "PRO"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


class AssetType(StrEnum):
    """What kind of media an asset holds (§10.17)."""

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    SUBTITLE = "SUBTITLE"
    DOCUMENT = "DOCUMENT"
    THUMBNAIL = "THUMBNAIL"


class AssetSourceType(StrEnum):
    """Where an asset came from (§10.17).

    This is provenance, and it matters beyond bookkeeping: a `USER_UPLOAD` is
    untrusted input that must be validated, while `AI_GENERATED` content was
    fetched from a provider and re-hosted by a worker (§27). Retention policy
    (§113) and the orphan collector (§163) both branch on it.
    """

    USER_UPLOAD = "USER_UPLOAD"
    AI_GENERATED = "AI_GENERATED"
    RENDERED = "RENDERED"
    DERIVED = "DERIVED"


class UploadStatus(StrEnum):
    """Where an asset sits in the two-phase upload handshake (§12).

    A presigned URL is handed out before any bytes exist, so a row is created
    in `PENDING` and only becomes `READY` once the server has confirmed the
    object is actually in storage and passed validation. Anything left
    `PENDING` is an abandoned upload for the GC to reclaim (§163).
    """

    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class WorkspaceRole(StrEnum):
    """Membership role (§40).

    Ordered from most to least privileged; :func:`role_rank` relies on that
    ordering for "at least this role" comparisons.
    """

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class Permission(StrEnum):
    """A single capability, checked at the point of use.

    Handlers ask for a *permission*, never a role. Roles change shape as the
    product grows — a scattered ``if role == ADMIN`` becomes wrong silently,
    whereas adding a permission to the matrix below is one visible edit.
    """

    # Workspace
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"

    # Membership
    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_UPDATE_ROLE = "member:update_role"
    MEMBER_REMOVE = "member:remove"

    # Billing (§40 — OWNER only)
    BILLING_MANAGE = "billing:manage"

    # Content
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"
    PRODUCT_DELETE = "product:delete"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    TEMPLATE_WRITE = "template:write"

    # Generation — costs money, so it is not implied by write access
    GENERATION_RUN = "generation:run"

    # Assets
    ASSET_UPLOAD = "asset:upload"
    ASSET_DOWNLOAD = "asset:download"


_VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.WORKSPACE_READ,
        Permission.MEMBER_READ,
        Permission.PRODUCT_READ,
        Permission.PROJECT_READ,
        # §40 leaves download "open according to policy"; granting read-only
        # users their own finished videos is the useful default, and the
        # per-workspace policy switch lands with billing plans.
        Permission.ASSET_DOWNLOAD,
    }
)

_EDITOR_PERMISSIONS: frozenset[Permission] = _VIEWER_PERMISSIONS | {
    Permission.PRODUCT_WRITE,
    Permission.PROJECT_WRITE,
    Permission.GENERATION_RUN,
    # Separate from PRODUCT_WRITE because an upload consumes storage the
    # workspace pays for, and because media outlives the product it was
    # uploaded against — the quota question is about the asset, not the owner.
    Permission.ASSET_UPLOAD,
}

_ADMIN_PERMISSIONS: frozenset[Permission] = _EDITOR_PERMISSIONS | {
    Permission.PRODUCT_DELETE,
    Permission.PROJECT_DELETE,
    Permission.TEMPLATE_WRITE,
    Permission.WORKSPACE_UPDATE,
    # "Partial member management" (§40): an admin may invite and remove, but
    # may not change roles — otherwise an admin could promote themselves to
    # owner, which is a privilege-escalation path rather than delegation.
    Permission.MEMBER_INVITE,
    Permission.MEMBER_REMOVE,
}

_OWNER_PERMISSIONS: frozenset[Permission] = _ADMIN_PERMISSIONS | {
    Permission.WORKSPACE_DELETE,
    Permission.MEMBER_UPDATE_ROLE,
    Permission.BILLING_MANAGE,
}

#: The authorisation matrix. One table, so the whole policy is auditable at a
#: glance rather than reconstructed from conditionals across the codebase.
ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.OWNER: _OWNER_PERMISSIONS,
    WorkspaceRole.ADMIN: _ADMIN_PERMISSIONS,
    WorkspaceRole.EDITOR: _EDITOR_PERMISSIONS,
    WorkspaceRole.VIEWER: _VIEWER_PERMISSIONS,
}

_ROLE_ORDER: tuple[WorkspaceRole, ...] = (
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.EDITOR,
    WorkspaceRole.VIEWER,
)


def role_rank(role: WorkspaceRole) -> int:
    """Seniority, 0 being most privileged."""
    return _ROLE_ORDER.index(role)


def role_has_permission(role: WorkspaceRole, permission: Permission) -> bool:
    """Whether ``role`` grants ``permission``."""
    return permission in ROLE_PERMISSIONS[role]


def permissions_for(role: WorkspaceRole) -> frozenset[Permission]:
    """Everything ``role`` may do."""
    return ROLE_PERMISSIONS[role]
