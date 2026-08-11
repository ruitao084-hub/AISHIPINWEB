# TASK STATUS

Living progress record for the AI Product Video Studio build
(taskbook §134, §175). Updated as each task completes — not batched at the end
of a phase.

- **Last updated:** 2026-08-11
- **Current phase:** PHASE 1 — Local Infrastructure (next up)
- **Last completed phase:** PHASE 0 — Repository Bootstrap ✅
- **Branch:** `claude/quirky-mendel-rlh1nm`

---

## Phase board

| Phase | Name                         | Status                          |
| ----- | ---------------------------- | ------------------------------- |
| 0     | Repository Bootstrap         | ✅ COMPLETED                    |
| 1     | Local Infrastructure         | ⬜ NOT_STARTED                  |
| 2     | Core Backend Foundation      | ⬜ NOT_STARTED                  |
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

## PHASE 0 — Repository Bootstrap

**Status: COMPLETED**

### Completed

| Task   | Delivered                                                                                                                                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P0-T01 | `.gitignore` (secrets, media, infra state), `README.md` (all 12 §133 sections), `TASK_STATUS.md`, `DEVLOG.md`, `.env.example` (full §7 set + §122 flags + §25 concurrency)                                                     |
| P0-T02 | Monorepo skeleton: `apps/{web,api,worker,render-worker}`, `packages/{ui,shared-types,config,prompts,backend-core,provider-contracts}`, `infra/`, `docs/`, `tests/`. pnpm workspace + Turborepo for JS; uv workspace for Python |
| P0-T03 | Next.js 16 App Router, TypeScript `strict` + `noUncheckedIndexedAccess`, Tailwind 4, shadcn/ui wired via `components.json`, ESLint flat config, Vitest + Testing Library                                                       |
| P0-T04 | FastAPI app factory with `/health` + `/ready` (§70), `packages/backend-core` shared package, Ruff (lint + format), mypy `strict`, pytest with integration/e2e/provider markers                                                 |
| P0-T05 | `Makefile` — `dev test lint typecheck build infra-up infra-down` plus `setup verify format test-cov`. GitHub Actions CI: web, api and secret-scan jobs                                                                         |
| Extra  | ADR-0001…0004 (§75), `docs/architecture/overview.md` (§5), Prettier config                                                                                                                                                     |

### Tests

| Gate          | Command                 | Result                                              |
| ------------- | ----------------------- | --------------------------------------------------- |
| Web lint      | `pnpm run lint`         | ✅ pass, 0 warnings                                 |
| Web typecheck | `pnpm run typecheck`    | ✅ pass                                             |
| Web unit      | `pnpm run test`         | ✅ 2 passed                                         |
| Web build     | `pnpm run build`        | ✅ compiled, 2 static routes                        |
| Python lint   | `ruff check .`          | ✅ all checks passed                                |
| Python format | `ruff format --check .` | ✅ 14 files formatted                               |
| Python types  | `mypy --strict`         | ✅ no issues in 7 files                             |
| Python unit   | `pytest`                | ✅ 6 passed, 0 warnings                             |
| Runtime       | `uvicorn` + curl        | ✅ `/health` 200, `/ready` 200, `/openapi.json` 200 |

### Acceptance (§77)

- [x] Web build succeeds
- [x] API starts successfully
- [x] Lint passes
- [x] Test commands exist and pass
- [x] README can guide a cold start

---

## PHASE 1 — Local Infrastructure (next)

**Status: NOT_STARTED**

| Task   | Scope                                 |
| ------ | ------------------------------------- |
| P1-T01 | PostgreSQL in Docker Compose          |
| P1-T02 | Redis in Docker Compose               |
| P1-T03 | MinIO in Docker Compose               |
| P1-T04 | SQLAlchemy 2 engine + connection pool |
| P1-T05 | Alembic initialisation                |
| P1-T06 | Redis client wrapper                  |
| P1-T07 | S3-compatible storage client (+ ADR)  |

**Acceptance:** automated tests proving DB connects, Redis set/get works, MinIO
upload/download works, and API health reports all three.

---

## Known issues

| #   | Issue                                                        | Impact                       | Plan                                                                                                                                                     |
| --- | ------------------------------------------------------------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Docker daemon is not started by default in the dev container | None — PHASE 1 is unblocked  | Resolved: `sudo dockerd` starts it (29.3.1, overlayfs). Must be started once per container session before `make infra-up`. Noted in the PHASE 1 runbook. |
| 2   | `ffmpeg` / `ffprobe` not installed locally                   | Blocks PHASE 13 verification | Render worker runs in its own image with FFmpeg installed; install locally before PHASE 13.                                                              |

Neither is caused by project code.

## Blocked on the user

Nothing right now. Development proceeds on mocks through PHASE 9.

| Will need                              | Phase | Why                                                                                 |
| -------------------------------------- | ----- | ----------------------------------------------------------------------------------- |
| Real video provider API key            | 10    | §21 requires one real provider working end to end. Everything before it uses mocks. |
| TTS provider key                       | 12    | Mock TTS carries PHASE 12 until then.                                               |
| LLM / Vision key                       | 6     | Mock vision provider carries PHASE 6 until then.                                    |
| Cloud accounts, domain, secret manager | 23    | Production deployment.                                                              |

## Technical debt

| #   | Item                                                                             | Incurred                                                    | Repayment            |
| --- | -------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------- |
| 1   | `packages/prompts` and `packages/provider-contracts` are documented placeholders | PHASE 0 — filling them now would be cross-phase development | PHASE 6 / PHASE 9    |
| 2   | `packages/shared-types` exports only a version constant                          | Generated client is P2-T08                                  | PHASE 2              |
| 3   | `packages/ui` holds only the `cn` helper                                         | Shared composites need real screens to share                | PHASE 5+             |
| 4   | `/ready` returns an empty dependency list                                        | No backing services exist yet                               | PHASE 1              |
| 5   | CI lacks integration, migration-from-empty, contract-drift and E2E gates         | Those subjects do not exist yet                             | PHASE 1 / 2 / 3 / 15 |
| 6   | Turborepo caches JS tasks only                                                   | Python gates currently run in ~1s                           | Revisit if CI slows  |

## Deviations from the taskbook

| Deviation                                       | Reason                                                                                                                                                                |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `httpx2` instead of `httpx` for the test client | Starlette 1.6 deprecates `httpx` in `TestClient`. Taskbook does not pin either; §0.1 rule 19 defers to current upstream guidance.                                     |
| No Google Fonts in the web app                  | `next/font/google` fetches at build time, making builds network-dependent and non-reproducible in CI (§68). System font stack with CJK fallbacks used instead (§128). |
| TypeScript pinned to 5.x, ESLint to 9.x         | TypeScript 7 (Go rewrite) and ESLint 10 are days old; `eslint-config-next@16.3.0` is validated against ESLint 9. Correctness over novelty (§180).                     |
