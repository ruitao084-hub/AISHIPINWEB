# ADR-0003: FastAPI as the backend framework

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 0 (P0-T04)

## Context

Taskbook §4.3 names FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic as the
baseline, and permits NestJS **only** when an existing project already commits
to a TypeScript backend. This repository was empty at PHASE 0, so no such prior
commitment exists.

§5.2 further requires the FastAPI OpenAPI document to be the single source of
truth for the HTTP contract, with the web client's types generated from it.

## Decision

Adopt FastAPI as specified, with:

- An **application factory** (`create_app()`) rather than a module-level
  singleton, so tests build isolated apps and PHASE 2 can inject settings and
  middleware without import-time side effects.
- Liveness (`/health`) and readiness (`/ready`) mounted at the root, outside the
  versioned surface, so orchestrator probes survive API version changes (§70).
- The business surface mounted under `/api/v1` (§41), added in P2-T05.

Liveness deliberately does **not** touch Postgres, Redis or S3: a dependency
blip should take the instance out of rotation via readiness, not get the
container killed and restarted by the liveness probe.

## Consequences

- Pydantic v2 models serve double duty as validation and as OpenAPI schema, so
  the §5.2 generated-client pipeline needs no separate schema definition.
- Python throughout means the API, the Celery worker and the FFmpeg render
  worker share one `backend_core` — the domain layer is written once.
- Async endpoints must avoid blocking calls. Provider SDKs that are sync-only
  get wrapped in a thread pool inside the adapter layer, never called directly
  from a route handler.

## Alternatives considered

- **NestJS** — would allow one language across the whole repo, but the taskbook
  restricts it to projects already committed to a TypeScript backend, and the
  render/QC pipeline leans on Python's media and imaging ecosystem.
- **Django + DRF** — heavier than needed; the ORM and admin bring conventions
  that fight the explicit repository/service layering §5.1 asks for.
- **Litestar** — attractive, but FastAPI is what the taskbook specifies and its
  OpenAPI tooling ecosystem is the one the §5.2 pipeline targets.
