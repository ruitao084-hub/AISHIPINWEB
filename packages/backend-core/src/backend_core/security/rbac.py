"""Permission checks (taskbook §40).

§40 is explicit that the backend enforces authorisation and that hiding a
button in the UI is not access control. Everything here is the server-side half.

Two rules the API layer depends on:

* Handlers ask for a **permission**, never a role. Roles change shape as the
  product grows; a scattered ``if role == ADMIN`` goes wrong silently, whereas
  the matrix in :mod:`backend_core.domain.enums` is one auditable table.
* A caller who is not a member of a workspace is told the workspace does not
  exist, not that they are forbidden. A 403 confirms the id is real, which
  leaks the existence of other tenants' resources.
"""

from __future__ import annotations

import uuid

from backend_core.domain.enums import Permission, WorkspaceRole, role_has_permission
from backend_core.errors import NotFoundError, WorkspaceForbiddenError


def ensure_permission(role: WorkspaceRole, permission: Permission) -> None:
    """Raise :class:`WorkspaceForbiddenError` unless ``role`` grants it.

    Used once membership is already established — the caller is a member, the
    question is only whether their role reaches far enough.
    """
    if not role_has_permission(role, permission):
        raise WorkspaceForbiddenError(
            "Your role does not permit this action.",
            details={"required_permission": permission.value, "your_role": role.value},
        )


def ensure_membership(role: WorkspaceRole | None, workspace_id: uuid.UUID) -> WorkspaceRole:
    """Return the caller's role, or raise as though the workspace were absent.

    ``None`` means "no membership row", which covers both a workspace that does
    not exist and one belonging to someone else. Both must produce the *same*
    404: distinguishing them turns workspace ids into an existence oracle and
    leaks tenant structure.
    """
    if role is None:
        raise NotFoundError("Workspace not found.", details={"workspace_id": str(workspace_id)})
    return role


def can(role: WorkspaceRole, permission: Permission) -> bool:
    """Non-raising check, for shaping a response rather than guarding one."""
    return role_has_permission(role, permission)
