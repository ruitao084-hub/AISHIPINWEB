# ADR-0004: Redis + Celery as the job queue

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 0 (recorded); implemented in PHASE 9

## Context

Video generation takes minutes and rendering takes longer. Taskbook §0.1 rule 7
and §179 ("Async First") forbid an HTTP request from ever waiting on either.
§22 fixes the shape: the API validates, creates a `GenerationJob`, reserves
credits, enqueues, and returns `202` with a `job_id`.

§4.5 designates Redis + Celery as the MVP default and states that Claude Code
**must not** substitute a second queue framework without first recording an ADR.
This ADR records the decision to keep the specified default.

## Decision

Redis as broker and result backend; Celery for workers. Temporal remains the
documented long-term option (§4.5) but is out of scope for the MVP.

Queues are **separate by workload** (§25) so a slow render cannot starve shot
generation, each with its own concurrency setting:

| Queue              | Env var                        | Consumed by          |
| ------------------ | ------------------------------ | -------------------- |
| `video_generation` | `VIDEO_GENERATION_CONCURRENCY` | `apps/worker`        |
| `tts`              | `TTS_CONCURRENCY`              | `apps/worker`        |
| `qc`               | `QC_CONCURRENCY`               | `apps/worker`        |
| `render`           | `RENDER_CONCURRENCY`           | `apps/render-worker` |

Constraints that hold regardless of broker, so the abstraction survives a
future move to Temporal:

- **Celery is transport, not truth.** Job state lives in the `generation_jobs`
  table (§10.15). A lost Redis message must never lose a job — the stuck-job
  reaper (§161, P16-T15) recovers from the database.
- **Idempotency** is enforced at the database level on
  `(workspace_id, idempotency_key)` (§23, §164), not by broker deduplication.
- **One worker per job**, guaranteed by a Redis lock or a DB row lock (§119).
- **Graceful shutdown** on SIGTERM: stop accepting new jobs, finish or record
  the current one, release locks, lose nothing (§118).

## Consequences

- Redis is required infrastructure for local development, provisioned by
  `make infra-up` in PHASE 1.
- Redis is not a durable queue. Because job state is authoritative in Postgres
  and a reaper reconciles it, a Redis outage delays work but does not lose it.
- Celery's own retry mechanism is not sufficient on its own: §24 distinguishes
  retryable (429, 5xx, network, download failure) from non-retryable (policy
  violation, bad format, insufficient credits) errors, so retry classification
  lives in `backend_core.jobs`, not in task decorators.

## Alternatives considered

- **Temporal** — better durability and visibility for multi-step workflows, and
  the taskbook's own long-term direction. Rejected for the MVP: it adds a
  server to operate before the core video pipeline exists at all.
- **RQ / Dramatiq** — simpler than Celery, but §4.5 names Celery, and switching
  would need a stronger reason than preference.
- **Postgres-only queue (SKIP LOCKED)** — one fewer moving part, and tempting
  given job state already lives in Postgres. Rejected because Redis is already
  required for caching, rate limiting (§123) and circuit-breaker health state
  (§56), so it is not an extra dependency.
