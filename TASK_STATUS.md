# TASK STATUS

Living progress record for the AI Product Video Studio build
(taskbook §134, §175). Updated as each task completes — not batched at the end
of a phase.

- **Last updated:** 2026-08-12
- **Current phase:** PHASE 7 — Project + Creative + Script (next up)
- **Last completed phase:** PHASE 6 — Product AI Analysis ✅
- **Branch:** `claude/quirky-mendel-rlh1nm`

---

## Phase board

| Phase | Name                         | Status                          |
| ----- | ---------------------------- | ------------------------------- |
| 0     | Repository Bootstrap         | ✅ COMPLETED                    |
| 1     | Local Infrastructure         | ✅ COMPLETED                    |
| 2     | Core Backend Foundation      | ✅ COMPLETED                    |
| 3     | Auth + Workspace + RBAC      | ✅ COMPLETED                    |
| 4     | Media + Upload + Storage     | ✅ COMPLETED                    |
| 5     | Product + Product Truth      | ✅ COMPLETED                    |
| 6     | Product AI Analysis          | ✅ COMPLETED                    |
| 7     | Project + Creative + Script  | ⬜ NOT_STARTED                  |
| 8     | Storyboard + Prompt Compiler | ⬜ NOT_STARTED                  |
| 9     | Job System + Mock Provider   | ⬜ NOT_STARTED                  |
| 10    | First Real Video Provider    | ⬜ NOT_STARTED · needs API key  |
| 11    | Shot Generation E2E          | ⬜ NOT_STARTED                  |
| 12    | TTS + Subtitle               | ⬜ NOT_STARTED                  |
| 13    | Timeline + FFmpeg Render     | ⬜ NOT_STARTED                  |
| 14    | QC                           | ⬜ NOT_STARTED                  |
| 15    | Web E2E Product Flow         | ⬜ NOT_STARTED                  |
| 16    | MVP Hardening                | ⬜ NOT_STARTED                  |
| 17    | Brand Kit + Template         | ⬜ NOT_STARTED                  |
| 18    | Credit + Cost                | ⬜ NOT_STARTED                  |
| 19    | Multi-provider Router        | ⬜ NOT_STARTED                  |
| 20    | Basic Editor                 | ⬜ NOT_STARTED                  |
| 21    | Batch SKU                    | ⬜ NOT_STARTED                  |
| 22    | Admin + Analytics            | ⬜ NOT_STARTED                  |
| 23    | Production Deployment        | ⬜ NOT_STARTED · needs accounts |
| 24    | Post-MVP Optimization        | ⬜ NOT_STARTED                  |

---

## PHASE 1 — Local Infrastructure

**Status: COMPLETED**

### Completed

| Task   | Delivered                                                                                                                                                                                       |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1-T01 | PostgreSQL 17 in `docker-compose.yml` with healthcheck, named volume, deterministic `--locale=C` collation                                                                                      |
| P1-T02 | Redis 8 with AOF persistence and healthcheck                                                                                                                                                    |
| P1-T03 | MinIO plus a one-shot `minio-init` that creates the bucket and sets `anonymous none` — buckets are never public (§110)                                                                          |
| P1-T04 | SQLAlchemy 2 async + sync engines sharing one `DATABASE_URL`, pool sizing, `pool_pre_ping`, transaction-owning session context managers                                                         |
| P1-T05 | Alembic in `infra/migrations`, URL sourced from settings (never `alembic.ini`), UTC timestamped revisions, ruff post-write hooks, constraint naming convention fixed before the first migration |
| P1-T06 | Async Redis client with pooling, health-check interval and lock/counter primitives                                                                                                              |
| P1-T07 | `ObjectStorage` Protocol + `S3ObjectStorage`, §11 key builders, presigned upload/download, ADR-0005                                                                                             |
| Extra  | `/ready` now probes all three services concurrently with a timeout; app lifespan disposes pools; `make migrate*` targets; `infra/scripts/init-local-env.sh`; CI integration job                 |

### Tests

57 passing (41 unit + 16 integration), zero warnings.

| Gate                      | Command                       | Result                    |
| ------------------------- | ----------------------------- | ------------------------- |
| Python lint               | `ruff check .`                | ✅ all checks passed      |
| Python format             | `ruff format --check .`       | ✅ 46 files               |
| Python types              | `mypy --strict`               | ✅ no issues, 17 files    |
| Unit                      | `pytest -m "not integration"` | ✅ 41 passed              |
| Integration               | `pytest -m integration`       | ✅ 16 passed              |
| Web lint/types/test/build | `pnpm`                        | ✅ unchanged from PHASE 0 |

### Acceptance (§78)

- [x] DB connects — plus session commit/rollback and 10 concurrent pooled connections
- [x] Redis set/get — plus atomic `INCR` and `SET NX` lock primitive (§119, §123)
- [x] MinIO upload/download — bytes and file round-trips, `head`, `exists`, idempotent delete, presigned URLs
- [x] API health — `/ready` returns 200 with all three dependencies up, 503 when any is down
- [x] Migrations apply to an empty database (§169)

---

## PHASE 2 — Core Backend Foundation

**Status: COMPLETED**

### Completed

| Task   | Delivered                                                                                                                                                                                  |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P2-T01 | Full Pydantic Settings over the whole `.env` surface, `SecretStr` for every credential, and boot-time production guards (JWT length, debug off, no wildcard or plain-http CORS, mocks off) |
| P2-T02 | `ErrorCode` closed enum carrying all 18 §65 codes; `AppError` hierarchy with HTTP status and §24 retryable classification                                                                  |
| P2-T03 | `RequestContextMiddleware` — id taken from a validated inbound header or generated, echoed back, bound for the request's lifetime                                                          |
| P2-T04 | JSON logging with correlation fields and redaction applied at the formatter: sensitive keys, URL-inline passwords, oversized base64, cycle-safe                                            |
| P2-T05 | `/api/v1` router with the shared error responses documented once                                                                                                                           |
| P2-T06 | `Base` plus `UUIDPrimaryKeyMixin`, `TimestampMixin`, `WorkspaceScopedMixin`, `SoftDeleteMixin`, `workspace_scoped_index`                                                                   |
| P2-T07 | Handlers for `AppError`, 422, Starlette HTTP errors and unhandled exceptions — every failure path returns the §41 envelope                                                                 |
| P2-T08 | `openapi.json` export, `openapi-typescript` generation, `make contract` / `make contract-check`, CI drift gate                                                                             |
| P2-T09 | `backend-core` §5.1 module tree established: domain, schemas, repositories, services, providers, prompts, jobs, security, observability                                                    |

### Tests

143 passing (127 unit + 16 integration), zero warnings.

| Gate                                       | Result                                                  |
| ------------------------------------------ | ------------------------------------------------------- |
| `ruff check` / `ruff format --check`       | ✅ 69 files                                             |
| `mypy --strict`                            | ✅ 34 source files                                      |
| `pytest` unit                              | ✅ 127 passed                                           |
| `pytest` integration                       | ✅ 16 passed                                            |
| `eslint` / `tsc` / `vitest` / `next build` | ✅ pass                                                 |
| `make contract-check`                      | ✅ verified it passes clean **and** fails on real drift |

### Acceptance (§79)

- [x] Integration tests written and passing
- [x] Every failure path returns the uniform envelope, including framework-generated ones
- [x] No secret, traceback or connection string reaches a client or a log

---

## PHASE 3 — Auth + Workspace + RBAC

**Status: COMPLETED**

### Completed

| Task           | Delivered                                                                                                                                                                   |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P3-T01/T06/T07 | `users`, `workspaces`, `workspace_members` (§10.1-10.3) with native Postgres enums, `(workspace_id, user_id)` unique constraint (§164), and the **first Alembic migration** |
| P3-T02         | Argon2id hashing (OWASP profile), length-only policy, NFKC normalisation, transparent rehash on login                                                                       |
| P3-T03/T04     | Register (creates a personal workspace in the same transaction), login, refresh, logout, `/me`                                                                              |
| P3-T05         | Short-lived JWT access token in memory + rotating HttpOnly cookie refresh token with `jti` revocation. ADR-0006                                                             |
| P3-T08         | Permission matrix (§40) enforced server-side; `require_permission` dependency; 404-not-403 for non-members                                                                  |
| P3-T09/T10     | Typed API client over the generated contract, auth context, login/register pages, protected `/app` layout                                                                   |
| Extra          | Login and registration rate limiting (§39, §123) with tests proving the limits still bite                                                                                   |

### Tests

203 passing (127 unit + 65 integration + 11 web), zero warnings.

| Gate                                      | Result                 |
| ----------------------------------------- | ---------------------- |
| `ruff check` / `ruff format --check`      | ✅ pass                |
| `mypy --strict`                           | ✅ 47 source files     |
| `pytest` unit                             | ✅ 127 passed          |
| `pytest` integration                      | ✅ 65 passed           |
| `vitest`                                  | ✅ 11 passed           |
| `eslint` / `tsc` / `next build`           | ✅ pass, 5 routes      |
| Migration `upgrade → downgrade → upgrade` | ✅ verified reversible |

### Acceptance (§80)

- [x] Register
- [x] Login
- [x] Refresh (with rotation; a replayed token is rejected)
- [x] OWNER / ADMIN / EDITOR / VIEWER matrix enforced server-side
- [x] **Unauthorised access fails** — a non-member gets 404, indistinguishable from a random id

---

## PHASE 4 — Media + Upload + Storage

**Status: COMPLETED**

### Completed

| Task   | Delivered                                                                                                                                                                                                                |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P4-T01 | `media_assets` (§10.17) — the only table that references binary content, with `AssetType` / `AssetSourceType` / `UploadStatus` native enums, a workspace-prefix CHECK constraint (§61), and the second Alembic migration |
| P4-T02 | `POST /uploads/presign` — closed MIME whitelist, per-type size limits, server-generated key (§11), `PENDING` row written before the bytes exist                                                                          |
| P4-T03 | `POST /uploads/{id}/complete` — HEAD, ranged signature read, probe, promote to `READY`; idempotent on retry (§67)                                                                                                        |
| P4-T04 | Magic-byte verification against the declared type, extension cross-check, size re-checked against the stored object                                                                                                      |
| P4-T05 | Pillow header probe: dimensions before decoding, decompression-bomb ceiling, format-confusion rejection, SHA-256                                                                                                         |
| P4-T06 | ffprobe adapter — fixed argv, per-call-site protocol whitelist, timeout, exact rational frame rates (`30000/1001`, not `29.97`)                                                                                          |
| P4-T07 | Drag-and-drop uploader with per-file progress, image previews, cancel and retry; `/app/media` library page                                                                                                               |
| Extra  | `read_prefix` ranged read on the storage Protocol; `GET /uploads/config` so the picker and the whitelist cannot drift; `GET /assets` and `GET /assets/{id}` with signed download URLs; ADR-0007                          |

### Tests

321 passing (197 unit + 94 integration + 30 web), zero warnings.

| Gate                                      | Result                              |
| ----------------------------------------- | ----------------------------------- |
| `ruff check` / `ruff format --check`      | ✅ pass                             |
| `mypy --strict`                           | ✅ 54 source files                  |
| `pytest` unit                             | ✅ 197 passed                       |
| `pytest` integration                      | ✅ 94 passed                        |
| `vitest`                                  | ✅ 30 passed                        |
| `eslint` / `tsc` / `next build`           | ✅ pass, 6 routes                   |
| `make contract-check`                     | ✅ pass                             |
| Migration `upgrade → downgrade → upgrade` | ✅ verified reversible (enums drop) |

### Acceptance (§81)

- [x] A browser uploads an image **straight to storage** — the PUT in the test
      bypasses the API entirely, exactly as §116 requires
- [x] A `MediaAsset` row is created, with probed width, height, size and SHA-256
- [x] Video uploads record duration, frame rate and codec via ffprobe over a
      signed URL, without the API holding the file

### Bugs found by tests, not by inspection

1. **A rejected upload's `FAILED` status was rolled back.** Rejection raises,
   and the request session rolls back on any exception — so the status write
   was undone while the object deletion (not transactional) went through,
   leaving a `PENDING` row pointing at nothing. The failure record is now
   committed in its own transaction. Caught by the cleanup test.
2. **`require_permission` had never been used by any route.** PHASE 3 enforced
   permissions inside services, so the dependency's `-> object` return type and
   its double-wrapped `Depends(...)` usage example were both wrong and nothing
   had exercised them. Wiring the first route through it surfaced both.
3. **A literal `%` in a CHECK constraint reached Postgres as `%%`.** Harmless in
   `LIKE` (two wildcards match as one) but wrong in the stored definition;
   replaced with `starts_with()`, which needs no metacharacter.

## PHASE 5 — Product + Product Truth

**Status: COMPLETED**

The phase the whole project's credibility rests on: §13 forbids the platform
from stating a product fact nobody confirmed, and this is where that becomes
structural rather than aspirational.

### Completed

| Task   | Delivered                                                                                                                                          |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| P5-T01 | `products` (§10.5) with the §104 state machine enforced by a transition table — `DRAFT → READY` is not a legal edge                                |
| P5-T02 | Product CRUD; `status` is deliberately not editable through PATCH (§105)                                                                           |
| P5-T03 | `product_assets` (§10.6) with roles, ordering, and exactly-one-primary enforced by a partial unique index                                          |
| P5-T04 | `product_facts` (§10.7); AI-sourced facts are forced to `AI_INFERRED` in the service, so no route can create a pre-verified AI fact                |
| P5-T05 | `product_claims` (§10.8) with cited evidence, claim types and default risk levels                                                                  |
| P5-T06 | Fact verify/reject/edit, recording who confirmed and when; a database CHECK refuses a `VERIFIED` row with no timestamp                             |
| P5-T07 | Claim verification requiring `VERIFIED` backing facts, plus `get_verified_claims` (§109) as the accessor generation code calls                     |
| P5-T08 | Product list, creation, and a detail page where facts and claims are verified — review state shown on every row rather than hidden behind a filter |
| Extra  | Withdrawal cascade: rejecting or editing a fact demotes every verified claim citing it. ADR-0008.                                                  |

### Tests

395 passing (239 unit + 117 integration + 39 web), zero warnings.

| Gate                                      | Result                           |
| ----------------------------------------- | -------------------------------- |
| `ruff check` / `ruff format --check`      | ✅ pass                          |
| `mypy --strict`                           | ✅ 58 source files               |
| `pytest` unit                             | ✅ 239 passed                    |
| `pytest` integration                      | ✅ 117 passed                    |
| `vitest`                                  | ✅ 39 passed                     |
| `eslint` / `tsc` / `next build`           | ✅ pass, 8 routes                |
| `make contract-check`                     | ✅ pass                          |
| Migration `upgrade → downgrade → upgrade` | ✅ verified reversible (8 enums) |

### Acceptance (§82)

- [x] Create a product
- [x] Upload several product images
- [x] Set the primary image
- [x] Edit facts
- [x] Confirm claims

### The rule, and what would have broken it

§13's forbidden example is a test: "Removes 99.9% of formaldehyde" is refused
verification for lacking evidence, while "Brings a little calm to your morning"
is approved because it asserts nothing checkable.

The subtle failure the phase had to close: verify a fact, verify a claim citing
it, then reject the fact. Naively the claim stays `VERIFIED` and a script keeps
quoting withdrawn evidence — the fabricated statement §13 forbids, reached one
legitimate step at a time. Rejecting or editing a fact now demotes every claim
that cited it.

### Bugs found by tests, not by inspection

1. **`create_fact` violated its own CHECK constraint.** It inserted a
   `VERIFIED` row and stamped `verified_at` in a _second_ statement, so the
   insert hit `ck_product_facts_verified_facts_have_a_timestamp` immediately.
   The constraint was written precisely so a partial verification could not
   exist, and it earned its place on the first run.
2. **The integration suite exhausted Postgres's connection limit.** Engines are
   cached per event loop and pytest-asyncio gives each test a fresh one, so
   every test built a pool nothing disposed. Invisible until the suite passed
   ~100 tests, then `FATAL: sorry, too many clients already`. The fixture now
   disposes what it opened.

## PHASE 6 — Product AI Analysis

**Status: COMPLETED**

### Completed

| Task   | Delivered                                                                                                                                                                                                                                                  |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P6-T01 | `VisionProvider` Protocol with `ProviderImage` / `ProviderUsage` / `VisionAnalysis`; §20's error taxonomy mapped at the adapter boundary                                                                                                                   |
| P6-T02 | Versioned prompt registry; `product_analyze_v1` v1; single-pass `{{name}}` substitution so an untrusted product name cannot inject a placeholder (§108)                                                                                                    |
| P6-T03 | `ProductIntelligence` / `VisualDNA`, `extra="forbid"`, and the `OBSERVED_FIELDS` / `INFERRED_FIELDS` split that makes §109's boundary structural                                                                                                           |
| P6-T04 | `MockVisionProvider` — deterministic from product name + image bytes, five failure modes via `MOCK_VISION_MODE` (§172), and `visible_text` left empty because a mock inventing legible text is exactly §13's danger                                        |
| P6-T05 | `AnthropicVisionProvider` — structured outputs, image downscaling to the model's resolution tier, refusal checked before content, full §20 error mapping. **Never run against the live API** (see below)                                                   |
| P6-T06 | `ProductAnalysisService.analyze` + `POST /products/{id}/analyze` + `GET /products/{id}/analyses`; `product_analyses` table records prompt key/version, model, tokens, latency and failures                                                                 |
| P6-T07 | Observed fields → `AI_INFERRED` facts, `possible_selling_points` → `SUGGESTED` claims. The inferred fields cannot reach `create_fact`: `_fact_specs` iterates `OBSERVED_FIELDS` only                                                                       |
| P6-T08 | Review UI: analysis panel with provenance, plus Verify / **Edit + Verify** / Reject per fact. A corrected value is stamped VERIFIED with the reviewer on it, while `source_type` stays `AI_VISION` — provenance and accountability are different questions |

### Tests

442 passing (307 unit + 135 integration), zero warnings. 67 new.

| Gate                 | Command                         | Result                        |
| -------------------- | ------------------------------- | ----------------------------- |
| Python lint          | `ruff check packages apps`      | ✅ all checks passed          |
| Python format        | `ruff format --check`           | ✅ 94 files                   |
| Python types         | `mypy --strict`                 | ✅ no issues, 65 files        |
| Unit                 | `pytest -m "not integration"`   | ✅ 307 passed                 |
| Integration          | `pytest -m integration`         | ✅ 135 passed                 |
| Migration round-trip | `upgrade → downgrade → upgrade` | ✅ plus `alembic check` clean |
| Web lint/types/build | `pnpm`                          | ✅                            |

### Acceptance (§83)

- [x] Uploading product images yields structured Product Intelligence
- [x] Every AI observation lands `AI_INFERRED` with `source_type=AI_VISION` — asserted, not assumed
- [x] `possible_selling_points` become `SUGGESTED` claims and **cannot** be approved without a verified fact (a 409 the test asserts, end to end)
- [x] The product lands `REVIEW_REQUIRED`, never `READY`
- [x] Each of the five injected provider failures leaves the product in `ASSETS_READY` with nothing written to the Truth Layer
- [x] A viewer cannot run an analysis — `GENERATION_RUN`, not `PRODUCT_WRITE` (§40)
- [x] The whole flow runs on mocks with no API key (§170)

### Two things worth carrying forward

**A real bug the tests found.** The product state machine had no
`REVIEW_REQUIRED → ANALYZING` edge, so a reviewer who added a clearer
photograph could not re-run the analyser — while a _finished_ (`READY`) product
could. §104 lists only the states, so the edge set was this project's design
and the omission was an oversight rather than policy. Added, with a domain test
naming the reason (§103 rules 4 and 10).

**A §108 hole in prompt rendering.** `Prompt.render` looped `str.replace` per
placeholder, so a product named `{{language}}` would be substituted _into_ the
template and then expanded by the next iteration — untrusted input reaching the
instruction text. Replaced with a single-pass regex substitution, which never
re-reads what it just wrote, plus a test on the real template.

## Known issues

| #   | Issue                                                                                                        | Impact                                                                                                                                | Plan                                                                                                                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Docker Hub's blob CDN (`production.cloudfront.docker.com`) is blocked by the environment's egress policy** | `make infra-up` cannot pull images _in this dev container_. Does not affect the compose file's correctness, CI, or any other machine. | Verified locally against natively installed Postgres 16 + Redis 8 and `moto` as the S3 endpoint; CI runs the real Postgres 17, Redis 8 and MinIO images. Not routed around, per the proxy's documented policy. |
| 2   | ~~`ffmpeg` / `ffprobe` not installed locally~~                                                               | —                                                                                                                                     | ✅ Resolved in PHASE 4: ffmpeg 6.1.1 installed locally and added to the CI integration job, since P4-T06 needs ffprobe. The probe tests **assert** the binary is present rather than skipping.                 |
| 3   | Local dev DB is Postgres 16 (apt) while compose and CI pin 17                                                | Low — no version-specific SQL in use                                                                                                  | CI is authoritative. Revisit if a 17-only feature is adopted.                                                                                                                                                  |

None is caused by project code.

## Blocked on the user

Nothing right now. Development proceeds on mocks through PHASE 9.

| Will need                              | Phase | Why                                                                                                                                         |
| -------------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Real video provider API key            | 10    | §21 requires one real provider working end to end.                                                                                          |
| TTS provider key                       | 12    | Mock TTS carries PHASE 12 until then.                                                                                                       |
| LLM / Vision key                       | 6     | **PHASE 6 is complete on mocks.** The Anthropic adapter is written and unit-tested but has never made a live call — see technical debt #12. |
| Cloud accounts, domain, secret manager | 23    | Production deployment.                                                                                                                      |

## Technical debt

| #   | Item                                                                             | Incurred                                                                                                                                                                                            | Repayment                                                                                                                                                                                    |
| --- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `packages/prompts` and `packages/provider-contracts` are documented placeholders | Filling them now would be cross-phase development                                                                                                                                                   | PHASE 6 / PHASE 9                                                                                                                                                                            |
| 2   | `packages/shared-types` exports only a version constant                          | Generated client is P2-T08                                                                                                                                                                          | PHASE 2                                                                                                                                                                                      |
| 3   | `packages/ui` holds only the `cn` helper                                         | Shared composites need real screens to share                                                                                                                                                        | PHASE 5+                                                                                                                                                                                     |
| 4   | ~~`/ready` returns an empty dependency list~~                                    | —                                                                                                                                                                                                   | ✅ Repaid in PHASE 1                                                                                                                                                                         |
| 5   | CI still lacks contract-drift and E2E gates                                      | Those subjects do not exist yet                                                                                                                                                                     | PHASE 2 / 15                                                                                                                                                                                 |
| 6   | Turborepo caches JS tasks only                                                   | Python gates run in ~2s                                                                                                                                                                             | Revisit if CI slows                                                                                                                                                                          |
| 7   | Compose runs infrastructure only; app services are not containerised             | Their Dockerfiles depend on the PHASE 2 settings layer                                                                                                                                              | PHASE 23 (§69)                                                                                                                                                                               |
| 8   | Storage uses single-PUT presigning, no multipart                                 | Covers the §12 limits (20 MB image / 500 MB video)                                                                                                                                                  | Revisit if limits rise                                                                                                                                                                       |
| 9   | **Video uploads carry no SHA-256** — only the storage ETag                       | Hashing means streaming the whole object through the API, which is what §116 and the presigned flow exist to avoid                                                                                  | PHASE 9: the ingest worker hashes while it already has the file. An integration test asserts `checksum is None` for video so the gap stays visible.                                          |
| 10  | Abandoned `PENDING` uploads leak a row and an orphan object                      | The §163 collector does not exist yet                                                                                                                                                               | PHASE 16 (§163). The partial index `(created_at) WHERE upload_status = 'PENDING'` it will scan is already in place.                                                                          |
| 11  | `complete` runs ffprobe synchronously inside a request                           | The job system arrives in PHASE 9; the acceptance criterion needs a synchronous answer                                                                                                              | Bounded by `media_probe_timeout_seconds` and run off the event loop. Candidate to become a job in PHASE 9.                                                                                   |
| 12  | **`AnthropicVisionProvider` has never run against the live API**                 | No key has been supplied. Request construction, downscaling, parsing and every error-mapping branch are tested against a stubbed client; whether the request shape is one the vendor accepts is not | Needs `ANTHROPIC_API_KEY` from the user. Until then `USE_MOCK_PROVIDERS=true` carries the whole flow (§170). Recorded in the module docstring too, so it cannot be rediscovered by surprise. |
| 13  | Analysis runs synchronously inside the request                                   | §83 permits it for a short task, and the job system is PHASE 9                                                                                                                                      | PHASE 9. The API already returns a `ProductAnalysis` row rather than the intelligence, so the async shape needs no client change beyond polling.                                             |
| 14  | The vendor JSON schema is hand-written beside the Pydantic model                 | Generating it from a model whose fields all have defaults produces a schema the structured-output API rejects                                                                                       | Kept in step by `test_the_schema_matches_the_pydantic_model`. Revisit if the vendor relaxes the `required` rule.                                                                             |

## Deviations from the taskbook

| Deviation                                                       | Reason                                                                                                                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `httpx2` instead of `httpx` for the test client                 | Starlette 1.6 deprecates `httpx` in `TestClient`. §0.1 rule 19 defers to current upstream guidance.                                                          |
| No Google Fonts in the web app                                  | `next/font/google` fetches at build time, making builds network-dependent and non-reproducible (§68). System font stack with CJK fallbacks instead (§128).   |
| TypeScript pinned to 5.x, ESLint to 9.x                         | TypeScript 7 and ESLint 10 are days old; `eslint-config-next@16.3.0` is validated against ESLint 9 (§180).                                                   |
| A minimal settings module arrived in PHASE 1 rather than P2-T01 | The PHASE 1 clients cannot read a database URL without one. Ad-hoc `os.environ` parsing, later ripped out, would be strictly worse. P2-T01 extends it.       |
| `moto` used as the local S3 endpoint                            | Only because MinIO's image and binary are both unreachable from this environment. CI always runs real MinIO, so nothing merges proven only against a double. |
