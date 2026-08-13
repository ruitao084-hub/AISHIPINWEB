# Development Log

Notable engineering changes and the reasoning behind them (taskbook §134).
Task-level progress lives in [`TASK_STATUS.md`](TASK_STATUS.md); formal
decisions live in [`docs/adr/`](docs/adr/). This file records the things that
are useful to know later but too small for an ADR.

---

## 2026-08-12 — PHASE 5: Product + Product Truth

The phase the project's credibility rests on. §13 forbids the platform from
stating a product fact nobody confirmed, and the only way to make that hold is
structurally — by the time a script is being written it is far too late to ask
whether a number is real.

### The rule cannot live at the point of generation

That was the design decision everything else follows from. A check inside the
script generator is a check some future code path skips, and by then the
fabricated number is already in the database. So the guarantee is a _shape_:
`get_verified_claims(product_id)` returns `VERIFIED` rows and nothing else,
and there is no filtering step for a caller to forget. Written up as ADR-0008.

Three rules, all in the service rather than the endpoint, because PHASE 6's
analyser will call the same service:

1. AI-sourced facts are forced to `AI_INFERRED`. A caller cannot insert a
   pre-verified AI fact even deliberately.
2. Promotion records who and when, with a database CHECK refusing a `VERIFIED`
   row that has no timestamp.
3. A claim asserting anything checkable needs a `VERIFIED` fact behind it, and
   the cited facts must belong to the same product.

`FUNCTIONAL` claims are _not_ exempt from rule 3, which is worth stating
because it looks like an over-restriction. §13's own permitted example —
"helps filter impurities" — is allowed only if that function has been confirmed
as a fact. A capability claim is an evidential claim. `EMOTIONAL` is the single
exemption: "brings a little calm to your morning" asserts nothing that could be
substantiated.

### The failure mode that decays silently

Verify a fact. Verify a claim citing it. Reject the fact.

Naively the claim stays `VERIFIED`, and a script goes on quoting evidence that
has been withdrawn. Every individual step was legitimate; the result is exactly
the fabricated statement §13 forbids. So rejecting a fact demotes every
verified claim citing it, and editing a fact's _value_ does the same — a claim
approved against "removes 99.9%" was not approved for "removes 50%". Editing
only the key or type leaves verification alone, because the assertion has not
changed.

This is the kind of rule that is never missed in review and always missing in
code, so it has its own test.

### A CHECK constraint earned its place on the first run

`create_fact` inserted a `VERIFIED` row and stamped `verified_at` in a _second_
statement — so the insert hit
`ck_product_facts_verified_facts_have_a_timestamp` immediately. The constraint
exists precisely so a half-written verification cannot exist, and it caught its
own author. The fix writes all three fields as part of the insert.

Worth noting because the tempting reaction to a constraint that fires during
development is to relax it. This one was right and the code was wrong.

### The integration suite ran out of Postgres connections

Adding PHASE 5's tests took the suite past ~100 and it started failing with
`FATAL: sorry, too many clients already` — in tests that passed individually
and in files that passed in pairs.

Cause: engines are cached per event loop (so a Celery worker calling
`asyncio.run` per job gets a working client — the fix from PHASE 1), and
pytest-asyncio gives every test a fresh loop. So every test built a connection
pool that nothing disposed. The app's lifespan disposes the pools it opens on
_its_ loop; nothing was disposing the ones the fixture opened on the test's.

The failure was latent from the moment the per-loop cache was introduced and
became visible only at a specific test count, which is the worst kind of
timing — it would have looked like PHASE 5 broke something unrelated.

### Vocabulary the taskbook left open

§10.7 and §10.8 name `fact_type`, `claim_type` and `risk_level` without
enumerating them. Chose values organised around _what would have to be true for
the statement to be honest_, since that is what decides whether evidence is
required — and so that §13's dangerous category (a quantified outcome) is
identifiable by type rather than by reading the text.

`FactSourceType` is kept distinct from `VerificationStatus` on purpose: a fact
can be `AI_VISION` in origin and `VERIFIED` in status, which is exactly the
review workflow §13 describes. Origin is never overwritten, so "a human
confirmed what the AI guessed" stays distinguishable from "a human typed it".

### Deferred, and why

`products.brand_kit_id` is in §10.5 but is not implemented. `brand_kits`
arrives in PHASE 17, and a nullable UUID with no foreign key is an
unconstrained column pretending to be a reference. It goes in with its table.

---

## 2026-08-12 — PHASE 4: Media + Upload + Storage

The phase where §116 stops being a principle and becomes a constraint the code
has to live with: the API is no longer allowed to see the file. Everything
below follows from that.

### The bytes go around the API, so validation happens afterwards

`presign` → browser PUTs to storage → `complete`. The interesting consequence
is that a malicious file genuinely exists in the bucket between the second and
third step. Three things contain it: the bucket is private (§110), nothing is
served from a `PENDING` asset, and a failed check deletes the object. Written
up properly in **ADR-0007** because it is the kind of tradeoff that looks like
an oversight to whoever reads the code next.

`complete` reads **64 bytes** to check the magic number, not the whole file.
The storage Protocol gained `read_prefix` for that — a ranged GET keeps the
check O(bytes examined) instead of O(file size), which for video is the
difference between 64 bytes and half a gigabyte.

### A rejected upload's FAILED status was being rolled back

The cleanup test failed with `PENDING` where it expected `FAILED`, and the
reason is worth writing down: rejection is _raised_, the request-scoped session
rolls back on any exception, so the status write was undone on the way out —
while the object deletion, not being transactional, went through anyway. The
result was a `PENDING` row pointing at nothing, which is worse than either
clean outcome: the client's retry finds no object, and the future GC cannot
tell an abandoned upload from a rejected one.

The failure record is now committed in its own transaction, the way an audit
entry is. The fact that the request failed is precisely why the record has to
survive it.

### `require_permission` had never actually been used

PHASE 3 wrote the dependency, documented it with a usage example, and then
enforced permissions inside the services instead. Wiring the first route
through it found two defects at once: the return type was `object` (FastAPI
wants `params.Depends`), and the docstring's example wrapped it in `Depends()`
a second time. Both would have been caught the first time it was used — which
is the point. This is the same shape as the PHASE 2 contract guard that only
ever ran green.

### A literal `%` in a CHECK constraint arrived as `%%`

`object_key LIKE 'workspaces/' || workspace_id::text || '/%'` reached Postgres
with a doubled wildcard. Semantically harmless — `%%` matches the same as `%` —
but wrong in the stored definition and confusing to read back. Replaced with
`starts_with()`, which expresses the same predicate with no metacharacter at
all. Found by querying `pg_get_constraintdef` after the migration rather than
by trusting that the DDL round-tripped.

### ffprobe is a subprocess, and subprocesses are an attack surface

Three guards, each for a real class of problem: the argv is a list and a source
beginning with `-` is refused (argument injection); `-protocol_whitelist` is
narrowed per call site, `file` for a path and `http,https,tcp,tls` for a URL,
so a crafted playlist cannot cross between them; and every invocation carries a
timeout, because a file whose index sits at the end forces a full seek.

Frame rates are parsed as rationals. `30000/1001` becomes `29.97002997…`, not
`29.97` — three decimals drift visibly across a multi-shot render.

### Images are checked before they are decoded

`Image.open` parses the header and stops, so dimensions are available before
any pixel data is touched. That is what makes the decompression-bomb ceiling a
cheap gate rather than a post-hoc check: a 400-megapixel PNG of one flat colour
is under 20 MB on the wire and would otherwise be decoded before anyone
noticed. There is an integration test that uploads exactly that.

### Video hashing is deferred, and the test says so

§12 asks for a file hash. Images get SHA-256 for free — the bytes are already
in memory for the dimension probe. Video does not, because hashing would mean
streaming the whole object through the API, which is the thing this entire
design exists to avoid. It moves to the ingest worker in PHASE 9. Rather than
leave that implicit, an integration test asserts `checksum is None` for video,
so the gap reads as a decision instead of as coverage.

### Client

`XMLHttpRequest` rather than `fetch` for the upload, for exactly one reason:
`fetch` still has no upload-progress event in any shipping browser. Everything
else in the client stays on `fetch`.

Three concurrent uploads, not unlimited — twenty parallel PUTs divide the same
bandwidth into twenty simultaneously-stalling transfers and make every progress
bar useless at once. Accepted types and limits come from `/uploads/config`
rather than a hardcoded list, so the picker's filter cannot drift from the
server's whitelist.

### ffmpeg in CI

The integration job now installs ffmpeg, and the probe test **asserts the
binary exists** rather than skipping when it is missing. A test that quietly
disappears is worse than no test: the phase that depends on it would look
covered. This also clears the PHASE 13 blocker early.

---

## 2026-08-11 — PHASE 3: Auth + Workspace + RBAC

The first phase with real database schema, real credentials and real
authorisation. Three bugs found, each by testing something rather than assuming
it.

### The migration was not reversible, and Alembic said nothing

§73 requires reversible migrations. The autogenerated `downgrade()` dropped all
three tables — and left four Postgres ENUM types behind, because `drop_table`
does not remove types created as a side effect of `create_table`. The next
`upgrade` then failed with `type "user_status" already exists`.

Nothing warns about this. It surfaced only because the round trip was actually
run: `upgrade → downgrade → upgrade`. Explicit `DROP TYPE IF EXISTS` statements
were added, the cycle re-run, and a checklist added to `script.py.mako` so the
next migration with an enum does not repeat it.

### A constraint violation masquerading as "email already registered"

Registration failed for any user whose display name was short — "A", or any
purely non-ASCII name. The response said _"An account with this email already
exists"_ for a brand-new address.

Two defects stacked. `slugify("A")` returned `"a"`, one character, violating the
`length(slug) >= 3` check constraint. And `register()` caught `IntegrityError`
and unconditionally re-raised it as `EmailAlreadyRegisteredError`, so a slug
failure was reported as an email collision.

The second is the worse one: it would have hidden every future constraint
violation behind a confidently wrong message. Now the handler reads the
violated constraint name from psycopg's diagnostics and only claims duplicate
email when that is genuinely what happened; anything else is logged and
re-raised. `slugify` pads short and non-ASCII names instead of producing
something the database will reject.

### 49 integration tests were silently running as unit tests

After adding the API suite the unit count jumped from 127 to 176. Those extra
tests need Postgres and Redis, and passed locally only because both happened to
be running. In CI's unit job, which has no services, they would all have failed.

The cause: `pytestmark = pytest.mark.integration` in a `conftest.py` applies
only to tests defined _in that conftest_, not to sibling modules. Replaced with
a `pytest_collection_modifyitems` hook that marks everything under the
directory. Split is now 127 unit / 65 integration, and the unit suite genuinely
runs with no services.

### Security decisions, and why

**Login cannot be used to enumerate accounts.** Unknown email, wrong password
and suspended account all return the identical error, and the no-such-user path
still performs an Argon2 verification against a dummy hash so the _timing_
matches too. A test asserts the code and message are byte-identical.

**A non-member gets 404, never 403.** A 403 confirms the id exists, which
leaks tenant structure. The test asserts a real-but-forbidden workspace id is
indistinguishable from a random UUID.

**Member lookups are scoped by workspace even when the member id is unique.**
Without the extra predicate, a member id from another workspace would resolve
and the permission check would have been run against the wrong tenant.

**An admin cannot invite an owner.** §40 grants admins "partial member
management"; letting that include granting OWNER would let an admin invite an
account they control and escalate. Inviting an owner requires the
role-change permission, which only owners hold.

**The last owner cannot be demoted or leave.** A workspace with no owner cannot
be billed, deleted, or have its membership managed.

**Argon2id over bcrypt** — memory-hard, and bcrypt silently truncates at 72
bytes, quietly weakening long passphrases. Details in ADR-0006.

### The rate limiter had to be worked around, so it got its own test

Every test registers from the same client address, so the 5-per-hour
registration limit correctly blocked the sixth test in the file. The counters
are now cleared between tests — but clearing a limit for convenience is exactly
how a limit stops being enforced, so `test_ratelimit.py` asserts that login and
registration limits still engage, and that a successful login resets the
counter so a user who mistypes cannot lock themselves out.

### Frontend

The access token lives in a module variable, never `localStorage`: storage
persists across tabs and reloads, so a token there can be harvested at leisure
by injected script. The refresh token is an HttpOnly cookie the client never
sees.

Concurrent 401s share one in-flight refresh. Because refresh _rotates_ the
token, five parallel requests firing five refreshes would invalidate four of
them and sign the user out — there is a test for exactly that.

`exactOptionalPropertyTypes` (enabled back in PHASE 0) caught `body: undefined`
being passed to `fetch`; the init object is now built conditionally.

The protected layout is a redirect, not a security boundary, and says so in a
comment — every request below it is authorised independently by the server.

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
