# TASK STATUS

Living progress record for the AI Product Video Studio build
(taskbook §134, §175). Updated as each task completes — not batched at the end
of a phase.

- **Last updated:** 2026-08-11
- **Current phase:** PHASE 3 — Auth + Workspace + RBAC (next up)
- **Last completed phase:** PHASE 2 — Core Backend Foundation ✅
- **Branch:** `claude/quirky-mendel-rlh1nm`

---

## Phase board

| Phase | Name                         | Status                          |
| ----- | ---------------------------- | ------------------------------- |
| 0     | Repository Bootstrap         | ✅ COMPLETED                    |
| 1     | Local Infrastructure         | ✅ COMPLETED                    |
| 2     | Core Backend Foundation      | ✅ COMPLETED                    |
| 3     | Auth + Workspace + RBAC      | ⬜ NOT_STARTED                  |
| 4     | Media + Upload + Storage     | ⬜ NOT_STARTED                  |
| 5     | Product + Product Truth      | ⬜ NOT_STARTED                  |
| 6     | Product AI Analysis          | ⬜ NOT_STARTED                  |
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

## PHASE 3 — Auth + Workspace + RBAC (next)

**Status: NOT_STARTED**

| Task   | Scope                            |
| ------ | -------------------------------- |
| P3-T01 | User schema + first migration    |
| P3-T02 | Password hashing (Argon2/bcrypt) |
| P3-T03 | Register                         |
| P3-T04 | Login (with rate limiting)       |
| P3-T05 | Access + refresh tokens (+ ADR)  |
| P3-T06 | Workspace schema                 |
| P3-T07 | Workspace membership             |
| P3-T08 | RBAC dependency                  |
| P3-T09 | Frontend login/register          |
| P3-T10 | Protected layout                 |

**Acceptance:** register, login, refresh, and OWNER/EDITOR/VIEWER permission
checks all tested — including that cross-workspace access fails.

---

## Known issues

| #   | Issue                                                                                                        | Impact                                                                                                                                | Plan                                                                                                                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Docker Hub's blob CDN (`production.cloudfront.docker.com`) is blocked by the environment's egress policy** | `make infra-up` cannot pull images _in this dev container_. Does not affect the compose file's correctness, CI, or any other machine. | Verified locally against natively installed Postgres 16 + Redis 8 and `moto` as the S3 endpoint; CI runs the real Postgres 17, Redis 8 and MinIO images. Not routed around, per the proxy's documented policy. |
| 2   | `ffmpeg` / `ffprobe` not installed locally                                                                   | Blocks PHASE 13 verification                                                                                                          | Render worker ships its own image with FFmpeg; install locally before PHASE 13.                                                                                                                                |
| 3   | Local dev DB is Postgres 16 (apt) while compose and CI pin 17                                                | Low — no version-specific SQL in use                                                                                                  | CI is authoritative. Revisit if a 17-only feature is adopted.                                                                                                                                                  |

None is caused by project code.

## Blocked on the user

Nothing right now. Development proceeds on mocks through PHASE 9.

| Will need                              | Phase | Why                                                |
| -------------------------------------- | ----- | -------------------------------------------------- |
| Real video provider API key            | 10    | §21 requires one real provider working end to end. |
| TTS provider key                       | 12    | Mock TTS carries PHASE 12 until then.              |
| LLM / Vision key                       | 6     | Mock vision provider carries PHASE 6 until then.   |
| Cloud accounts, domain, secret manager | 23    | Production deployment.                             |

Also outstanding, not blocking: the repository's **default branch is still
`claude/quirky-mendel-rlh1nm`** and should be switched to `main` in
Settings → General. No available tool can change repository settings.

## Technical debt

| #   | Item                                                                             | Incurred                                               | Repayment              |
| --- | -------------------------------------------------------------------------------- | ------------------------------------------------------ | ---------------------- |
| 1   | `packages/prompts` and `packages/provider-contracts` are documented placeholders | Filling them now would be cross-phase development      | PHASE 6 / PHASE 9      |
| 2   | `packages/shared-types` exports only a version constant                          | Generated client is P2-T08                             | PHASE 2                |
| 3   | `packages/ui` holds only the `cn` helper                                         | Shared composites need real screens to share           | PHASE 5+               |
| 4   | ~~`/ready` returns an empty dependency list~~                                    | —                                                      | ✅ Repaid in PHASE 1   |
| 5   | CI still lacks contract-drift and E2E gates                                      | Those subjects do not exist yet                        | PHASE 2 / 15           |
| 6   | Turborepo caches JS tasks only                                                   | Python gates run in ~2s                                | Revisit if CI slows    |
| 7   | Compose runs infrastructure only; app services are not containerised             | Their Dockerfiles depend on the PHASE 2 settings layer | PHASE 23 (§69)         |
| 8   | Storage uses single-PUT presigning, no multipart                                 | Covers the §12 limits (20 MB image / 500 MB video)     | Revisit if limits rise |

## Deviations from the taskbook

| Deviation                                                       | Reason                                                                                                                                                       |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `httpx2` instead of `httpx` for the test client                 | Starlette 1.6 deprecates `httpx` in `TestClient`. §0.1 rule 19 defers to current upstream guidance.                                                          |
| No Google Fonts in the web app                                  | `next/font/google` fetches at build time, making builds network-dependent and non-reproducible (§68). System font stack with CJK fallbacks instead (§128).   |
| TypeScript pinned to 5.x, ESLint to 9.x                         | TypeScript 7 and ESLint 10 are days old; `eslint-config-next@16.3.0` is validated against ESLint 9 (§180).                                                   |
| A minimal settings module arrived in PHASE 1 rather than P2-T01 | The PHASE 1 clients cannot read a database URL without one. Ad-hoc `os.environ` parsing, later ripped out, would be strictly worse. P2-T01 extends it.       |
| `moto` used as the local S3 endpoint                            | Only because MinIO's image and binary are both unreachable from this environment. CI always runs real MinIO, so nothing merges proven only against a double. |
