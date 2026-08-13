"""Prove every route is guarded (§38, §59, P16-T01).

A permission audit written as prose goes stale the day someone adds a route.
This one is executable: it walks the mounted application and reports any route
that reaches a handler without authentication or without a permission check.

It is a function rather than a test so that both a test *and* `make audit-perms`
can call it, and so a reviewer can run it against a running app.

**What counts as guarded.** A route is guarded when its dependency tree
contains `get_current_user` — directly, or through `get_workspace_role`, or
through the closure `require_permission` builds. Walking the tree rather than
matching decorator text is what makes the check trustworthy: a router-level
dependency guards its routes just as well as a per-route one, and a textual
check would miss that and produce false alarms nobody would keep running.

**What is deliberately public**, and why each one is safe:

- `/health`, `/ready` — orchestrator probes. They report dependency reachability
  and nothing about any tenant's data.
- `/openapi.json`, `/docs`, `/redoc` — the contract. Disabled in production by
  `create_app`; the schema describes shapes, never rows.
- `/api/v1/` — reports which feature flags are on, which the web client needs
  before it has a token.
- `/api/v1/auth/register`, `/login`, `/refresh` — the endpoints that mint a
  token cannot require one. All three are rate limited (§123).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from aipvs_api.dependencies import get_current_user, get_workspace_role

#: Paths that are intentionally reachable without a token. Exact matches, not
#: prefixes — a prefix entry would silently exempt anything added beneath it.
PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/health",
        "/ready",
        "/live",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/docs/oauth2-redirect",
        "/api/v1/",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        # Logout takes the refresh cookie, not a bearer token, and succeeds
        # even when the token is already invalid — a logout that fails leaves
        # someone believing they are signed out when they are not.
        "/api/v1/auth/logout",
    }
)

#: Authenticated routes that legitimately have no *workspace* permission,
#: because they are about the caller rather than about a tenant's data.
NO_PERMISSION_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "/api/v1/auth/me",
        # Listing your own workspaces, and creating one, cannot require a role
        # inside a workspace you are not yet in.
        "/api/v1/workspaces",
        "/api/v1/workspaces/{workspace_id}/uploads/config",
    }
)

#: The platform-admin surface (§99). Guarded by `require_platform_admin`, which
#: is not a workspace permission and deliberately cannot be — the question "may
#: this person read every tenant's jobs" has nothing to do with any role inside
#: one. Recognised by prefix because every route beneath it carries the same
#: guard, applied per route.
ADMIN_PREFIX: Final[str] = "/api/v1/admin/"

#: Routes whose permission check lives in the service rather than in a route
#: dependency, verified by reading them.
#:
#: This is not a way to wave routes through. Each one is here because its rule
#: cannot be expressed as a single permission on the route:
#:
#: - `GET /workspaces/{id}` returns 404 rather than 403 for a non-member, so
#:   ids cannot be probed across tenants. A route-level check would answer 403
#:   and confirm existence.
#: - `DELETE /members/{id}` needs `member:remove` to remove *someone else* and
#:   no permission at all to leave yourself. One permission cannot say that.
#: - The remaining member routes call `ensure_permission` in
#:   `WorkspaceService` alongside rules a dependency cannot see, such as
#:   refusing to demote the last owner.
#:
#: Adding a path here is a claim that someone read the handler. The audit
#: cannot check that claim, which is why the list is short and annotated.
SERVICE_LEVEL_PERMISSION: Final[frozenset[str]] = frozenset(
    {
        "/api/v1/workspaces/{workspace_id}",
        "/api/v1/workspaces/{workspace_id}/members",
        "/api/v1/workspaces/{workspace_id}/members/{member_id}",
    }
)


@dataclass(frozen=True, slots=True)
class RouteGuard:
    """What protects one route."""

    path: str
    methods: tuple[str, ...]
    name: str
    authenticated: bool
    permission_checked: bool
    #: True when the route is guarded by `require_platform_admin` instead of a
    #: workspace permission.
    admin_only: bool = False

    @property
    def public(self) -> bool:
        return self.path in PUBLIC_PATHS


@dataclass(frozen=True, slots=True)
class AuditReport:
    routes: tuple[RouteGuard, ...]

    @property
    def unauthenticated(self) -> tuple[RouteGuard, ...]:
        """Routes reachable without a token that are not on the public list."""
        return tuple(route for route in self.routes if not route.authenticated and not route.public)

    @property
    def unchecked(self) -> tuple[RouteGuard, ...]:
        """Authenticated routes with no permission check and no exemption."""
        return tuple(
            route
            for route in self.routes
            if route.authenticated
            and not route.permission_checked
            and not route.admin_only
            and route.path not in NO_PERMISSION_REQUIRED
            and route.path not in SERVICE_LEVEL_PERMISSION
        )

    @property
    def ok(self) -> bool:
        return not self.unauthenticated and not self.unchecked

    def describe(self) -> str:
        if self.ok:
            return f"{len(self.routes)} routes audited; every one is guarded."

        lines: list[str] = []
        for route in self.unauthenticated:
            lines.append(f"UNAUTHENTICATED  {'/'.join(route.methods)} {route.path}")
        for route in self.unchecked:
            lines.append(f"NO PERMISSION    {'/'.join(route.methods)} {route.path}")
        return "\n".join(lines)


def _walk_routes(routes: Sequence[BaseRoute], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Yield `(full_path, route)` for every route, through included routers.

    `app.routes` is not flat. `include_router` leaves a `_IncludedRouter`
    wrapper that holds the original router on `original_router` and the mount
    prefix on `include_context.prefix`; the routes underneath keep their
    *relative* paths. Iterating only the top level finds four routes on this
    app and none of the hundred that matter — which would have made this audit
    report "every route is guarded" while checking nothing.

    The prefix is accumulated rather than read off the route, because a route's
    own `path` is relative to however many routers it was included through.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
            continue

        included = getattr(route, "original_router", None)
        if included is None:
            continue
        context = getattr(route, "include_context", None)
        nested_prefix = f"{prefix}{getattr(context, 'prefix', '')}"
        yield from _walk_routes(included.routes, nested_prefix)


def audit_permissions(app: FastAPI) -> AuditReport:
    """Walk `app`'s routes and report what guards each one."""
    guards: list[RouteGuard] = []

    for path, route in _walk_routes(app.routes):
        calls = _dependency_callables(route)
        # `require_permission` returns a closure named `dependency` defined in
        # `aipvs_api.dependencies`. Identifying it by module and qualname is
        # what lets the walk see through the factory without exporting the
        # inner function purely so this check can import it.
        permission_checked = any(
            getattr(call, "__module__", "") == "aipvs_api.dependencies"
            and getattr(call, "__qualname__", "").startswith("require_permission.")
            for call in calls
        )
        admin_only = any(
            getattr(call, "__module__", "") == "aipvs_api.v1.admin"
            and getattr(call, "__name__", "") == "require_platform_admin"
            for call in calls
        )
        authenticated = (
            get_current_user in calls
            or get_workspace_role in calls
            or permission_checked
            or admin_only
        )

        guards.append(
            RouteGuard(
                path=path,
                methods=tuple(sorted(route.methods or set())),
                name=route.name,
                authenticated=authenticated,
                permission_checked=permission_checked,
                admin_only=admin_only,
            )
        )

    return AuditReport(routes=tuple(guards))


def _dependency_callables(route: APIRoute) -> set[Any]:
    """Every callable in a route's dependency tree, transitively.

    Transitive because the guards nest: `require_permission` depends on
    `get_workspace_role`, which depends on `get_current_user`. Checking only
    the top level would report a permission-guarded route as unauthenticated.
    """
    seen: set[Any] = set()
    stack = list(route.dependant.dependencies)

    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            seen.add(dependant.call)
        stack.extend(dependant.dependencies)

    return seen


def main() -> int:
    """`python -m aipvs_api.permissions_audit` — exits non-zero on a finding."""
    from aipvs_api.app import create_app

    report = audit_permissions(create_app())
    import sys

    print(report.describe(), file=sys.stderr)  # noqa: T201 — this is a CLI
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ADMIN_PREFIX",
    "NO_PERMISSION_REQUIRED",
    "PUBLIC_PATHS",
    "SERVICE_LEVEL_PERMISSION",
    "AuditReport",
    "RouteGuard",
    "audit_permissions",
]
