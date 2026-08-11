# `aipvs-backend-core`

Shared Python core for the API, the job worker and the render worker.

Taskbook §5.1 forbids the three apps from each carrying their own copy of the
domain, provider and repository code. They import it from here; only the HTTP
controllers, the Celery entrypoint and the FFmpeg entrypoint stay app-local.

## Module tree

Established by **P2-T09**:

| Module           | Holds                                                                  |
| ---------------- | ---------------------------------------------------------------------- |
| `domain/`        | Entities, enums, state machines (Product §104, Project §105, Job §106) |
| `schemas/`       | Pydantic models, including validated AI output schemas (§107)          |
| `repositories/`  | Data access. All queries workspace-scoped (§9)                         |
| `services/`      | Domain services — the only layer allowed to change business state      |
| `providers/`     | LLM / Vision / Image / Video / TTS / Moderation adapters (§20)         |
| `prompts/`       | Prompt registry with versioning (§15)                                  |
| `storage/`       | S3-compatible object storage client (§4.6, §11)                        |
| `jobs/`          | Job orchestration, idempotency, retry, state transitions (§22–§24)     |
| `security/`      | Hashing, tokens, RBAC checks, SSRF-safe fetching (§39–§40, §61)        |
| `observability/` | Structured logging, trace/job id propagation, metrics (§63–§64)        |

## Boundary rules

- Provider adapters **must not** change `Project` status, touch credits, write
  storyboards, or render video (§20). They map parameters, call, map status and
  errors, and report cost metadata — nothing else.
- Nothing here may import from `aipvs_api`, `aipvs_worker` or
  `aipvs_render_worker`. The dependency arrow points one way only.
