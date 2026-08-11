# Development Log

Notable engineering changes and the reasoning behind them (taskbook §134).
Task-level progress lives in [`TASK_STATUS.md`](TASK_STATUS.md); formal
decisions live in [`docs/adr/`](docs/adr/). This file records the things that
are useful to know later but too small for an ADR.

---

## 2026-08-11 — PHASE 2: Core Backend Foundation

Config, errors, logging, the versioned surface, ORM base models, and the
OpenAPI → TypeScript contract pipeline.

### A guard that silently passed

`make contract-check` regenerates the client and fails if the result differs
from what is committed. Written, run, reported "API contract is up to date" —
and it was lying. It used `git diff`, which does not report **untracked**
files, and the generated artefacts had never been committed. Any first-time
generation would have sailed through.

It only surfaced because the check was tested by deliberately changing the API
and confirming the guard failed. It did not. Switched to
`git status --porcelain`, which reports untracked, modified and staged paths
alike, then re-ran both cases: clean passes, injected drift fails.

The lesson is narrow and worth keeping: a guard that has only ever been
observed passing has not been tested. Both branches need exercising.

### Errors are a contract, not strings

§65 fixes eighteen error codes, so `ErrorCode` is a `StrEnum` and a test
asserts every taskbook code exists. Clients branch on `code` — HTTP status is
too coarse and messages are for humans.

`retryable` lives on the error rather than in the retry loop, because whether a
429 is worth retrying is a property of what went wrong, not of who is handling
it. PHASE 9's runner reads it instead of re-deriving the classification.

Two message decisions are security, not wording: `InvalidCredentialsError`
deliberately does not say whether the email or the password was wrong (the
distinction turns login into an account-enumeration oracle), and the 422
handler keeps the field name and reason from Pydantic but drops the submitted
value — otherwise a mistyped password ends up in a log and on a screen.

### Nothing escapes the envelope

Handlers cover `AppError`, `RequestValidationError`, Starlette's own
`HTTPException` and bare `Exception`. Without the last three, a client would
meet three different error shapes — a 404 from the router, a 422 from FastAPI,
a 500 from anywhere — and two of them would carry no `code` at all.

An unhandled exception returns `INTERNAL_ERROR` and the `request_id`, nothing
more. Tests assert that a `RuntimeError` carrying
`postgresql://svc:hunter2@db/aipvs` yields a response containing neither the
password, the scheme, the exception type, nor the word "Traceback".

### Redaction belongs at the formatter

Trusting every call site to remember not to log a payload fails once, and once
is enough to put a provider key in an aggregator. So redaction runs inside
`JsonFormatter`: sensitive key names at any nesting depth, passwords inline in
connection URLs, base64 runs over 256 characters, and a depth cap so a cyclic
structure cannot hang the logger. Logging must never be able to take down the
process it describes.

### Correlation via contextvars

Request and job ids would otherwise thread through every signature.
`ContextVar` is safe here specifically because it is per-task under asyncio — a
module global or thread-local would let concurrent requests read each other's
ids. There is a test running two overlapping tasks that asserts exactly this.

The inbound `X-Request-ID` is honoured so a trace can span the web app and the
API, but it is validated first: it lands in every log line, so an unchecked
value could inject newlines and forge JSON log records.

### Small things worth recording

**`rootDir` is not the only tsconfig trap** — `Base.metadata` needed
`ClassVar[MetaData]`, since `DeclarativeBase` declares it as a class variable
and a plain annotation redeclares it as an instance attribute.

**`T201` scoped rather than suppressed.** The `print` ban exists for services
(§63); `infra/scripts/export_openapi.py` is a CLI whose interface _is_ stdout.
A per-file ignore says that; a `noqa` would just have hidden it.

**A shell quoting bug worth not repeating.** Nine placeholder `__init__.py`
files were generated with a `write()` helper whose single-quoted arguments were
never closed, so every other file swallowed the next command. The alternating
failure pattern gave it away. Regenerated through Python, which has no such
ambiguity, and verified all nine parse.

---

## 2026-08-11 — PHASE 1: Local Infrastructure

Compose stack plus the database, Redis and object storage clients, with
integration tests that talk to real services.

### The bug worth remembering: event-loop affinity

The first integration run failed with `RuntimeError: Event loop is closed` on
every Redis test after the first. The cause was the client being cached with
`lru_cache` — but an asyncio Redis connection pool **binds to the event loop
that created it**, so a single process-wide client is only correct in a process
that runs exactly one loop.

The tempting fix was a test fixture resetting the cache between tests. That
would have hidden a real defect: a Celery task calling `asyncio.run()` per job
runs a fresh loop each time and would have hit exactly this in production, far
from any test. Fixed properly by caching per running loop in a
`WeakKeyDictionary`, so entries die with their loop. The same treatment was
applied to the async SQLAlchemy engine and sessionmaker, which carry the same
constraint — those tests happened to pass, but for incidental reasons.

For the API, which runs one long-lived loop, the behaviour is unchanged: one
engine, one pool.

### Docker Hub is unreachable from this environment

`production.cloudfront.docker.com` is denied by the environment's egress
policy, so no image can be pulled here. Per the proxy's documented guidance,
this was reported rather than routed around.

The compose file is still the real deliverable and CI exercises it properly.
For a local loop, Postgres and Redis were installed natively and `moto` serves
the S3 API on MinIO's port — no branching in test code, because it is the same
API at the same address. CI runs the genuine Postgres 17, Redis 8 and MinIO
images, so nothing merges having been proven only against a double.

### Decisions worth remembering

**One `DATABASE_URL`, two engines.** SQLAlchemy's psycopg3 dialect drives both
sync and async from an identical URL, so Alembic and the workers (sync) and the
API (async) share one connection string. The worker pool is deliberately half
the API's: workers hold a connection for a whole job, and a fleet of them each
grabbing an API-sized pool would exhaust Postgres' connection limit.

**Constraint naming convention set before the first migration.** Postgres
auto-names unnamed constraints and Alembic then cannot emit a reliable
`DROP CONSTRAINT`. Fixing this after migrations exist means rewriting every
constraint later; doing it on an empty database costs nothing.

**Storage keys are built, never accepted.** Filenames are server-generated
UUIDs and extensions come from a MIME whitelist, so path traversal, null bytes
and double extensions are impossible by construction rather than by sanitising.
Presigned uploads sign the `Content-Type`, so a client cannot sign for a JPEG
and store HTML under that key.

**Liveness and readiness diverged further.** `/ready` now probes all three
services concurrently with a 3s budget — three sequential timeouts would blow
most orchestrators' probe window. Probes never raise, and report the exception
_type_ only: an exception message routinely contains a connection string with a
password (§63). There is a test asserting a password cannot reach the response.

**Local `.env` bootstrapping moved into a script.** `.env.example` keeps every
secret blank so the CI secret scan stays meaningful; `make setup` generates a
real `JWT_SECRET` and fills MinIO's local credentials into the gitignored
`.env`. Idempotent — it only fills values that are empty.

### Verified

41 unit + 16 integration tests, ruff, ruff format and `mypy --strict` all clean.
Integration coverage proves: the database round-trips and rolls back, ten
concurrent pooled connections work, Redis provides atomic counters and a
`SET NX` lock, object storage round-trips both bytes and files with working
presigned URLs, migrations apply to an empty database, and `/ready` returns 200
with every dependency live.

---

## 2026-08-11 — PHASE 0: Repository Bootstrap

Empty repository → working polyglot monorepo with every quality gate green.

### Structure

Two coordinated workspaces rather than one (ADR-0001): pnpm + Turborepo for
JavaScript, uv for Python, joined by a root `Makefile` that is the documented
interface for both. All Python tool configuration is centralised in the root
`pyproject.toml` so the four Python packages cannot drift apart.

`packages/backend-core` is a real uv workspace member from the first commit, so
the API and both workers resolve one shared core. Taskbook §5.1 asks for this;
`test_workspace_wiring.py` asserts it, including that nothing in `backend_core`
imports back into an app package.

### Decisions worth remembering

**Dropped `next/font/google`.** The generated app fetched Geist from Google
Fonts at build time, making every build network-dependent — bad for CI
reproducibility (§68) and for air-gapped builds. Replaced with a system font
stack that includes CJK fallbacks (`Noto Sans SC`, `PingFang SC`,
`Microsoft YaHei`), which the product needs anyway for zh-CN (§128).

**Pinned TypeScript 5.x and ESLint 9.x.** The registry offers TypeScript 7 (the
Go rewrite) and ESLint 10, both days old. `eslint-config-next@16.3.0` is
validated against ESLint 9. §180 ranks correctness above development speed;
these are cheap upgrades once the ecosystem catches up.

**Swapped `httpx` → `httpx2`.** Starlette 1.6 emits a deprecation warning when
`TestClient` runs on `httpx`. Rather than suppress the warning, took the
upstream path (§0.1 rule 19). Test suite now runs clean with zero warnings.

**Health and readiness are genuinely different endpoints.** `/health` never
touches a backing service — if it did, a transient Postgres blip would get
containers killed and restarted instead of merely removed from rotation.
`/ready` is where dependency probes go, and it returns 503 when any fails.

**Removed `vite-tsconfig-paths`.** Vitest 4's Vite resolves tsconfig paths
natively via `resolve.tsconfigPaths`. §137 says not to carry a dependency for
something the platform already does.

### Phase discipline

Several directories are deliberately placeholders: `packages/prompts`,
`packages/provider-contracts`, and the `backend_core` module tree. Filling them
now would be cross-phase development, and they are specified well enough
(§5.1, §15, §140) that guessing early risks building the wrong shape. Each
carries a README naming the task that fills it. Tracked as technical debt items
1–3 with explicit repayment phases.

The `infra-up` / `infra-down` Make targets exist as P0-T05 requires, and fail
with a message pointing at PHASE 1 rather than a confusing Docker error.

### CI

Three jobs: web (lint → build → typecheck → test → format), api (ruff → format
→ mypy → pytest → coverage), and a secret scan that fails the build if `.env`
is ever tracked or if `.env.example` gains a filled-in secret value (§7, §173).

Build runs _before_ typecheck in the web job — Next.js 16 generates route types
(`LayoutProps`, `PageProps`) into `.next/types` that `tsc --noEmit` needs.
Documented in the README troubleshooting table since it is genuinely surprising.

### Verified

Lint, format, typecheck, unit tests and build all pass on both halves with zero
warnings, and the API was started for real and probed over HTTP — `/health`,
`/ready` and `/openapi.json` all returned 200.
