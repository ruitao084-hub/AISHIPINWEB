"""Domain layer — entities, enums and state machines.

The rules that stay true regardless of how data is stored or exposed: the
Product (§104), Project (§105) and Job (§106) state machines, the role model
(§40), and the verification states that make the Truth Layer work (§13).

State transitions are validated here, never by assigning a string to a status
column — §105 is explicit that arbitrary status writes are forbidden.

Importing this package registers every model with the shared metadata, which
is what lets Alembic autogeneration see the full schema.
"""

from backend_core.domain.enums import (
    ROLE_PERMISSIONS,
    Permission,
    PlanCode,
    UserStatus,
    WorkspaceRole,
    WorkspaceStatus,
    permissions_for,
    role_has_permission,
    role_rank,
)
from backend_core.domain.models import User, Workspace, WorkspaceMember

__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "PlanCode",
    "User",
    "UserStatus",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WorkspaceStatus",
    "permissions_for",
    "role_has_permission",
    "role_rank",
]
