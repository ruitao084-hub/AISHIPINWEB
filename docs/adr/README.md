# Architecture Decision Records

Each ADR records one decision, why it was taken, and what it costs. They are
append-only: superseding an ADR means writing a new one that references it, not
editing history.

Format: **Context → Decision → Consequences → Alternatives considered.**

## Index

| ADR                                          | Title                                                              | Status   | Phase |
| -------------------------------------------- | ------------------------------------------------------------------ | -------- | ----- |
| [0001](0001-monorepo-tooling.md)             | Monorepo tooling: pnpm + Turborepo + uv workspace                  | Accepted | 0     |
| [0002](0002-python-package-manager.md)       | uv as the Python package and environment manager                   | Accepted | 0     |
| [0003](0003-backend-framework.md)            | FastAPI as the backend framework                                   | Accepted | 0     |
| [0004](0004-queue-framework.md)              | Redis + Celery as the job queue                                    | Accepted | 0     |
| [0005](0005-object-storage.md)               | S3-compatible object storage, MinIO locally                        | Accepted | 1     |
| [0006](0006-auth-token-strategy.md)          | Short JWT access + rotating cookie refresh                         | Accepted | 3     |
| [0007](0007-upload-and-media-validation.md)  | Two-phase presigned upload and media validation                    | Accepted | 4     |
| [0008](0008-product-truth-layer.md)          | The Product Truth Layer                                            | Accepted | 5     |
| [0009](0009-vision-provider-and-analysis.md) | Vision provider adapter, prompt registry and the analysis boundary | Accepted | 6     |

## Required but not yet written

Taskbook §75 requires an ADR for each of the following. They are written in the
phase that actually makes the decision — writing them earlier would be guessing.

| Decision                              | Owning phase                                   |
| ------------------------------------- | ---------------------------------------------- |
| Provider abstraction (video adapters) | PHASE 9 (P9-T09) — the vision half is ADR-0009 |
| Timeline schema                       | PHASE 13 (P13-T01)                             |
| Render architecture                   | PHASE 13 (P13-T05)                             |
| Credits transaction model             | PHASE 18 (P18-T02)                             |
