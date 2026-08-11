# ADR-0001: Monorepo tooling — pnpm + Turborepo + uv workspace

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 0 (P0-T02)

## Context

Taskbook §5 requires one repository holding a Next.js web app, a FastAPI API,
two Python workers, and shared packages. §4.1 recommends pnpm workspace plus
Turborepo, and permits adjustment provided an ADR records it.

The repository is therefore **polyglot**: no single tool manages both the
JavaScript and the Python halves. §5.1 additionally forbids the three Python
apps from vendoring their own copies of the domain code — they must share one
importable core.

## Decision

Two coordinated workspaces, one per language, joined by a root `Makefile`:

- **JavaScript** — pnpm workspace (`pnpm-workspace.yaml`) with Turborepo for
  task orchestration and caching. Members: `apps/web`, `packages/ui`,
  `packages/shared-types`, `packages/config`.
- **Python** — uv workspace declared in the root `pyproject.toml`. Members:
  `apps/api`, `apps/worker`, `apps/render-worker`, `packages/backend-core`.
  A single root `.venv` and one `uv.lock` cover all four.
- **Entry point** — `make <target>` is the documented interface. Contributors
  and CI run the same commands; neither needs to know which half a target hits.

All Python tool configuration (Ruff, mypy, pytest, coverage) lives in the root
`pyproject.toml` so the four Python packages cannot drift apart.

## Consequences

- One `uv sync --all-packages` gives every Python app the same `backend_core`,
  satisfying §5.1 structurally rather than by convention. A test asserts it
  (`packages/backend-core/tests/test_workspace_wiring.py`).
- Two lockfiles (`pnpm-lock.yaml`, `uv.lock`) must both be committed, and CI
  installs with `--frozen-lockfile` / `--frozen` so drift fails the build.
- Turborepo caches only the JS tasks. Python gates are fast enough that adding
  a second cache layer is not yet worth the complexity; revisit if CI slows.
- Contributors need both Node 22+ and Python 3.11+ installed. `make setup`
  installs everything from a clean checkout.

## Alternatives considered

- **Nx** — richer generators and a Python plugin, but a much larger surface for
  what is currently four Python packages and one Next.js app. §137 says not to
  pull in a large framework for a small need.
- **A single tool (Poetry or Rye) driving everything** — neither manages the JS
  half, so the polyglot split does not actually disappear.
- **Separate repositories per app** — would break the shared-core requirement
  and force version coordination across repos for every domain change.
