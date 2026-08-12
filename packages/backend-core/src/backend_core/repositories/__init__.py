"""Data access.

Every query against a workspace-scoped table filters by ``workspace_id`` (§9).
Repositories return domain objects rather than leaking ORM rows upward, so a
service cannot accidentally trigger lazy loading across a transaction
boundary.
"""

from backend_core.repositories.users import UserRepository, normalize_email
from backend_core.repositories.workspaces import WorkspaceRepository, slugify

__all__ = ["UserRepository", "WorkspaceRepository", "normalize_email", "slugify"]
