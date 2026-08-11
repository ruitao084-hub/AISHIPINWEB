# ADR-0002: uv as the Python package and environment manager

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 0 (P0-T04)

## Context

Taskbook §4.3 specifies Python, FastAPI, Pydantic v2, SQLAlchemy 2 and Alembic,
but does not name a package manager. §5.1 requires a shared `backend-core`
package that three sibling apps depend on by path, and §68 requires a
reproducible CI install.

## Decision

Use **uv** with a workspace declared in the root `pyproject.toml`.

- One resolved `uv.lock` for every Python package; CI installs with
  `uv sync --all-packages --frozen`.
- `packages/backend-core` is declared a workspace source, so the three apps
  depend on it by name and get the live local copy — no `pip install -e` steps,
  no `PYTHONPATH` manipulation, no path dependencies duplicated per app.
- All packages use a `src/` layout with `hatchling` as the build backend, so
  tests import the installed package rather than shadowing it from the CWD.

## Consequences

- Dependency resolution across four packages is a single locked operation, so
  the API and workers cannot silently end up on different library versions.
- Docker images can install straight from `uv.lock` for reproducible builds.
- Adding a dependency means editing the owning package's `pyproject.toml` and
  re-running `uv sync`; the root lockfile changes and must be committed.
- uv is a comparatively young tool. The escape hatch is cheap: the
  `pyproject.toml` files are standard PEP 621, so switching to Poetry or pip
  means replacing the lockfile, not restructuring the repository.

## Alternatives considered

- **Poetry** — mature and well understood, but its workspace/monorepo story
  relies on path dependencies declared per-app, which is exactly the duplication
  §5.1 warns about, and resolution across four packages is markedly slower.
- **pip + requirements.txt per app** — no cross-package resolution at all;
  nothing prevents the API and worker from drifting onto different versions of
  a shared library, which for shared domain code is a correctness risk.
- **PDM** — comparable feature set; uv chosen for resolution speed and because
  its workspace model maps directly onto the §5.1 shared-core requirement.
