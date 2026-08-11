"""Shared FastAPI dependencies: session, current user, workspace role.

These are the single choke point for authentication and authorisation. A route
that forgets to depend on :func:`get_current_user` is unauthenticated, so the
dependency is declared on the router rather than repeated per handler wherever
a whole router is protected.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import Permission, WorkspaceRole
from backend_core.domain.models import User
from backend_core.errors import UnauthorizedError
from backend_core.observability import set_context
from backend_core.repositories.workspaces import WorkspaceRepository
from backend_core.security import ensure_membership, ensure_permission
from backend_core.services.auth import AuthService

# auto_error=False so a missing header raises our own UnauthorizedError in the
# §41 envelope rather than Starlette's bare 403 with a different shape.
_bearer = HTTPBearer(auto_error=False, scheme_name="Bearer")


async def get_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed on success.

    Owning the transaction here means a handler cannot leave one half-open on
    an error path, and every write in a request is atomic together.
    """
    async with get_async_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the bearer token to an active user.

    Binds ``user_id`` into the logging context, so every subsequent log line in
    the request carries it without the handler passing it around (§63).
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication required.")

    user = await AuthService(session).get_authenticated_user(credentials.credentials)

    set_context(user_id=str(user.id))
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_workspace_role(
    workspace_id: Annotated[uuid.UUID, Path()],
    user: CurrentUser,
    session: SessionDep,
) -> WorkspaceRole:
    """The caller's role in the path's workspace, or 404 if not a member.

    Non-membership and non-existence produce the same 404 by design: a 403
    would confirm the workspace exists, letting ids be probed across tenants.
    """
    role = await WorkspaceRepository(session).get_role(workspace_id, user.id)
    role = ensure_membership(role, workspace_id)
    set_context(workspace_id=str(workspace_id))
    return role


WorkspaceRoleDep = Annotated[WorkspaceRole, Depends(get_workspace_role)]


def require_permission(permission: Permission) -> object:
    """Build a dependency asserting the caller holds ``permission``.

    Handlers declare the capability they need rather than the roles that happen
    to have it today, so widening a role is one edit to the matrix in
    :mod:`backend_core.domain.enums` instead of a hunt through conditionals.

    Usage::

        @router.delete(
            "/{workspace_id}",
            dependencies=[Depends(require_permission(Permission.WORKSPACE_DELETE))],
        )
    """

    async def dependency(role: WorkspaceRoleDep) -> WorkspaceRole:
        ensure_permission(role, permission)
        return role

    return Depends(dependency)
