# Architecture Decision Records

Each ADR records one decision, why it was taken, and what it costs. They are
append-only: superseding an ADR means writing a new one that references it, not
editing history.

Format: **Context → Decision → Consequences → Alternatives considered.**

## Index

| ADR                                    | Title                                             | Status   | Phase |
| -------------------------------------- | ------------------------------------------------- | -------- | ----- |
| [0001](0001-monorepo-tooling.md)       | Monorepo tooling: pnpm + Turborepo + uv workspace | Accepted | 0     |
| [0002](0002-python-package-manager.md) | uv as the Python package and environment manager  | Accepted | 0     |
| [0003](0003-backend-framework.md)      | FastAPI as the backend framework                  | Accepted | 0     |
| [0004](0004-queue-framework.md)        | Redis + Celery as the job queue                   | Accepted | 0     |

## Required but not yet written

Taskbook §75 requires an ADR for each of the following. They are written in the
phase that actually makes the decision — writing them earlier would be guessing.

| Decision                                                  | Owning phase                        |
| --------------------------------------------------------- | ----------------------------------- |
| Storage provider (S3-compatible abstraction, local MinIO) | PHASE 1 (P1-T07)                    |
| Auth token strategy (access/refresh, cookie vs header)    | PHASE 3 (P3-T05)                    |
| Provider abstraction (the adapter contract itself)        | PHASE 9 (P9-T09) / PHASE 6 (P6-T01) |
| Timeline schema                                           | PHASE 13 (P13-T01)                  |
| Render architecture                                       | PHASE 13 (P13-T05)                  |
| Credits transaction model                                 | PHASE 18 (P18-T02)                  |
