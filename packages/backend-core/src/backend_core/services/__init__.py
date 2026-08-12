"""Domain services — the only layer permitted to change business state.

Controllers translate HTTP; providers talk to vendors; services decide. This
is the boundary §20 protects when it forbids a provider adapter from touching
project status or credits.
"""

from backend_core.services.auth import (
    AuthService,
    EmailAlreadyRegisteredError,
    RegistrationResult,
    TokenPair,
)
from backend_core.services.workspaces import (
    AlreadyMemberError,
    LastOwnerError,
    WorkspaceService,
    WorkspaceWithRole,
)

__all__ = [
    "AlreadyMemberError",
    "AuthService",
    "EmailAlreadyRegisteredError",
    "LastOwnerError",
    "RegistrationResult",
    "TokenPair",
    "WorkspaceService",
    "WorkspaceWithRole",
]
