# Architecture Overview

> Maintained document (taskbook §5). Update it whenever a boundary moves.

## What the system is

A pipeline that turns **real product photos** into **truthful product videos**.
The emphasis on truthful is architectural, not editorial: the system is built so
that an AI model _cannot_ invent a product specification and have it reach an
advertising script.

```
Product → Product Intelligence → Truth Layer → Creative → Script
       → Storyboard → Prompt Compiler → Provider Adapter → Job Orchestrator
       → Media Ingestion → TTS/Subtitle → Timeline → Render → QC → Export
```

## Processes

| Process       | Package                 | Responsibility                                                                                     |
| ------------- | ----------------------- | -------------------------------------------------------------------------------------------------- |
| Web           | `apps/web`              | Next.js App Router UI. Talks only to the API. Holds no provider keys.                              |
| API           | `apps/api`              | HTTP controllers. Validates, authorises, enqueues, returns. Never waits on AI work.                |
| Worker        | `apps/worker`           | Celery consumer for `video_generation`, `tts`, `qc`.                                               |
| Render Worker | `apps/render-worker`    | FFmpeg compositing. Separate because it is CPU/disk-bound and long-running (§71).                  |
| Shared core   | `packages/backend-core` | Domain, services, repositories, providers, prompts, storage, jobs, security, observability (§5.1). |

Dependency arrow: **apps → backend-core**, never the reverse. Enforced by a test
in `packages/backend-core/tests/test_workspace_wiring.py`.

## The two boundaries that matter most

### 1. Truth boundary (§13, §109)

Everything an AI infers enters as `AI_INFERRED`. Only a human action —
user-provided, user-confirmed, or admin-reviewed — promotes a fact to
`VERIFIED`. The script generator reads `get_verified_claims(product_id)` and is
structurally unable to read `possible_selling_points`.

Consequence in practice: with no verified data, the system may still generate
_visual_ creative, but may not state "removes 99.9% of formaldehyde". It can
say "helps filter impurities" only if that capability is itself a verified fact.

### 2. Provider boundary (§20)

```
Business services  ──► ProviderAdapter (Protocol) ──► Runway / Veo / Sora / Mock
```

An adapter may: map parameters, call the provider, map status, map errors,
report cost metadata, return a result URL.

An adapter may **not**: change `Project` status, touch credits, write
storyboards, or render video. Those belong to services, so swapping a provider
never rewrites business logic.

Every provider has a **Mock** counterpart supporting success, failure, timeout
and cancel (§21, §172). `USE_MOCK_PROVIDERS=true` runs the entire pipeline with
no API keys at all — §170 makes this a hard requirement, not a convenience.

## Async job flow (§22)

```
HTTP  ─► validate ─► create GenerationJob ─► reserve credits ─► enqueue ─► 202 + job_id

Worker ─► pop ─► lock ─► submit to provider ─► persist ProviderJob
       ─► poll/webhook ─► download result ─► validate media ─► store to S3
       ─► create MediaAsset ─► complete job ─► capture credits
```

Three rules this shape exists to guarantee:

1. **No synchronous waiting.** The API returns in under a second (§72).
2. **No provider URL is ever permanent.** Results are downloaded, `ffprobe`d and
   re-hosted in our own storage before anything references them (§27).
3. **Non-destructive regeneration.** A retry creates a _new_ job; the previous
   asset survives and `shots.selected_generation_job_id` chooses the winner
   (§144).

## Credits boundary (§22, §52)

`CreditService` is defined in PHASE 9 but backed by `NoopCreditService` with
`ENABLE_CREDITS=false`. The job orchestrator depends on the interface from day
one and never touches credit tables directly; PHASE 18 swaps in the real ledger
without changing the orchestrator. This keeps the boundary correct without
letting an unbuilt billing system block the video pipeline.

## Storage (§11)

Postgres stores metadata and object keys only — never binaries (§9). Objects are
workspace-isolated with server-generated UUID filenames; user-supplied filenames
are never trusted:

```
workspaces/{workspace_id}/products/{product_id}/originals/{uuid}.{ext}
workspaces/{workspace_id}/projects/{project_id}/shots/{shot_id}/{uuid}.mp4
workspaces/{workspace_id}/projects/{project_id}/renders/{render_id}.mp4
```

Uploads go **browser → S3 directly** via presigned URL; large media never
proxies through the API (§12, §116). Downloads use short-lived signed URLs
against a private bucket (§110).

## Contract between web and API (§5.2)

```
FastAPI route + Pydantic models
  → /openapi.json
  → generated TypeScript client (packages/shared-types/src/generated/)
  → apps/web
```

Hand-written request/response types in pages are prohibited — they are exactly
the drift this pipeline exists to prevent. CI fails when the checked-in
generated output diverges from the live schema (P2-T08).

## Current state

PHASE 0 complete: toolchain, workspaces, quality gates, CI. The API serves
`/health`, `/ready` and `/openapi.json` and nothing else yet. See
[`TASK_STATUS.md`](../../TASK_STATUS.md) for phase-by-phase progress.
