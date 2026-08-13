# AI Product Video Studio

Turn real product photos into truthful, ready-to-publish product videos.

Upload product images, confirm what is actually true about the product, and the
platform generates a creative plan, script, storyboard, per-shot video,
voiceover, subtitles and a final composited MP4 — with every AI claim traceable
back to a fact a human verified.

> **Status: PHASE 0 complete** (repository bootstrap). The API currently serves
> health, readiness and its OpenAPI document only. See
> [`TASK_STATUS.md`](TASK_STATUS.md) for what is built and what is next, and
> [`docs/architecture/overview.md`](docs/architecture/overview.md) for how the
> pieces fit together.

## Why it is built this way

Three principles drive most of the design decisions:

- **Truth first.** AI inference enters the system as `AI_INFERRED` and can only
  become `VERIFIED` through a human action. Scripts are generated from verified
  claims alone, so the model cannot invent a specification and have it reach an
  advertisement.
- **Async first.** Video generation and rendering take minutes. No HTTP request
  ever waits on them — the API creates a job, enqueues it, and returns a
  `job_id` to poll.
- **Provider agnostic.** Every model vendor sits behind an adapter. Swapping a
  video provider never touches business logic, and a mock provider runs the
  entire pipeline with no API keys.

---

## 1. Prerequisites

| Tool             | Version | Notes                                                              |
| ---------------- | ------- | ------------------------------------------------------------------ |
| Node.js          | ≥ 22    |                                                                    |
| pnpm             | ≥ 10    | `corepack enable pnpm`                                             |
| Python           | ≥ 3.11  |                                                                    |
| uv               | ≥ 0.8   | [install](https://docs.astral.sh/uv/getting-started/installation/) |
| Docker + Compose | ≥ 24    | Needed from PHASE 1 for Postgres, Redis, MinIO                     |
| FFmpeg + ffprobe | ≥ 6     | Needed from PHASE 4: `ffprobe` reads every uploaded video          |
| GNU Make         | any     | Entry point for every command                                      |

Verify:

```bash
node -v && pnpm -v && python3 -V && uv --version && docker --version && ffprobe -version | head -1
```

## 2. Install

```bash
git clone https://github.com/ruitao084-hub/AISHIPINWEB.git
cd AISHIPINWEB
make setup      # installs JS + Python dependencies and creates .env
```

`make setup` is idempotent and will not overwrite an existing `.env`.

## 3. Environment

Configuration lives in `.env`, created from [`.env.example`](.env.example).
`.env` is gitignored and must never be committed.

Nothing needs a real API key to develop: `USE_MOCK_PROVIDERS=true` is the
default and runs the full pipeline offline.

The one value worth setting immediately:

```bash
# .env — generate a real secret even locally
JWT_SECRET=$(openssl rand -hex 32)
```

**Rules:** secrets exist only in server-side environment variables; no provider
key ever reaches the browser; only `NEXT_PUBLIC_*` values are exposed to the
client, and those must be non-secret by definition.

## 4. Infrastructure

```bash
make infra-up      # Postgres + Redis + MinIO
make infra-logs    # tail logs
make infra-down    # stop (volumes preserved)
```

Starts PostgreSQL 17, Redis 8 and MinIO. The bucket is created automatically
and set to private — buckets are never public (§110).

| Service       | Address                                                      |
| ------------- | ------------------------------------------------------------ |
| PostgreSQL    | `localhost:5432` (`postgres` / `postgres`, database `aipvs`) |
| Redis         | `localhost:6379`                                             |
| MinIO API     | http://localhost:9000                                        |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`)          |

Application containers are not in the compose stack yet — run the apps on the
host with `make dev` against this infrastructure. Their Dockerfiles arrive with
production packaging in PHASE 23.

<details>
<summary>If you cannot pull Docker images</summary>

Some restricted networks block Docker Hub's blob CDN. Postgres and Redis can be
installed natively, and `moto` (already a dev dependency) serves the S3 API on
MinIO's port:

```bash
sudo apt-get install -y postgresql redis-server
sudo pg_ctlcluster 16 main start && sudo -u postgres createdb aipvs
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"
sudo redis-server --daemonize yes
uv run moto_server -H 127.0.0.1 -p 9000 &
```

No test or application code changes — it is the same API at the same address.
CI always runs the real images, so nothing merges proven only against a double.

</details>

## 5. Database migrations

```bash
make migrate           # apply all migrations
make migrate-new m="add products table"
make migrate-down      # roll back one revision
```

Also available: `make migrate-status` (current revision and history) and
`make migrate-sql` (print the SQL without applying it — use this to review a
change before it touches production).

The schema is only ever changed through a migration: never by hand, and never
against a production database directly (§73). The database URL comes from
settings, not from `alembic.ini`, so there is one connection string and no
credentials in a tracked file.

> No migrations exist yet — the first real schema arrives with users and
> workspaces in **PHASE 3**. CI already asserts the chain applies to an empty
> database (§169).

## 6. Seed data

```bash
make seed
```

Creates a demo workspace, product, brand kit, template and mock credits so the
frontend has something to render.

> Built in **PHASE 3+** as the entities it seeds come into existence.

## 7. Development

```bash
make dev        # web + API together
make dev-web    # http://localhost:3000
make dev-api    # http://localhost:8000
```

| Surface              | URL                                     |
| -------------------- | --------------------------------------- |
| Web                  | http://localhost:3000                   |
| API docs (Swagger)   | http://localhost:8000/docs              |
| API docs (ReDoc)     | http://localhost:8000/redoc             |
| OpenAPI schema       | http://localhost:8000/openapi.json      |
| Liveness / readiness | http://localhost:8000/health · `/ready` |

## 8. Testing

```bash
make verify            # lint + typecheck + test + build — run before committing
make test              # all unit tests
make test-web          # vitest
make test-api          # pytest (unit only)
make test-integration  # pytest integration suite; needs `make infra-up`
make test-cov          # coverage report
```

Coverage targets (§67): ≥ 80% on core domain, ≥ 90% on credits and anything
financial. Coverage is a signal, not the goal.

## 9. Mock providers

`USE_MOCK_PROVIDERS=true` (the default) makes every AI call resolve locally
against fixtures in `tests/fixtures/`. A complete run — analyse → creative →
script → storyboard → shots → voice → render → QC → download — works with no
account anywhere.

Failure injection for testing error paths:

```bash
MOCK_VIDEO_MODE=success   # default
MOCK_VIDEO_MODE=fail      # provider rejects the job
MOCK_VIDEO_MODE=timeout   # job never completes
MOCK_VIDEO_MODE=slow      # completes, but slowly
```

> Mock providers land in **PHASE 6** (vision) and **PHASE 9** (video).

## 10. Real providers

Real providers are opt-in per capability:

```bash
USE_MOCK_PROVIDERS=false
ENABLE_REAL_VIDEO_PROVIDER=true
DEFAULT_VIDEO_PROVIDER=<provider>
RUNWAY_API_KEY=...          # whichever provider you enabled
```

Keys are read server-side only. Adding a provider means implementing the adapter
contract plus its mock, error mapping, cost capture and tests — the checklist is
in taskbook Appendix C.

> The first real video provider lands in **PHASE 10**.

## 11. Rendering

The render worker downloads shot videos, voice and subtitles, builds an FFmpeg
plan from the project `Timeline`, and produces an MP4 (H.264 / AAC / yuv420p /
faststart) plus a thumbnail.

FFmpeg is always invoked with an argument array, never `shell=True`, with
server-generated paths, an isolated temp directory per render, and a timeout.

> Built in **PHASE 13**.

## 12. Troubleshooting

| Symptom                                                              | Cause and fix                                                                                                                                                     |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MediaProbeError: ffprobe not found`                                 | Install FFmpeg (`apt-get install ffmpeg`), or point `FFPROBE_PATH` at it. Video uploads cannot be validated without it (§12).                                     |
| `docker pull` fails with 403 from `production.cloudfront.docker.com` | Your network blocks Docker Hub's CDN. See "If you cannot pull Docker images" in §4.                                                                               |
| `RuntimeError: Event loop is closed` from Redis or the DB            | You are sharing an async client across event loops. Use `get_redis()` / `get_async_engine()`, which cache per loop, rather than holding a module-level reference. |
| Integration tests fail with connection refused                       | Infrastructure is not running. `make infra-up`, then `make test-integration`.                                                                                     |
| `pnpm install` fails on a frozen lockfile                            | Dependencies changed — run `pnpm install` without `--frozen-lockfile` and commit the updated `pnpm-lock.yaml`.                                                    |
| `uv sync --frozen` fails                                             | Same for Python — run `uv sync --all-packages` and commit `uv.lock`.                                                                                              |
| `tsc` cannot find `LayoutProps` / `PageProps`                        | Next.js generates route types during build. Run `pnpm run build` once, then `make typecheck`.                                                                     |
| Port 3000 or 8000 already in use                                     | `lsof -ti:3000 \| xargs kill` (likewise 8000).                                                                                                                    |
| Ruff or mypy passes locally, fails in CI                             | CI uses locked versions. Run `make install` to match.                                                                                                             |
| API import errors after adding a package                             | Re-run `uv sync --all-packages`.                                                                                                                                  |

## Production

Deploying this is not `make dev` with different environment variables. Read
[`docs/operations/production.md`](docs/operations/production.md) — it covers
secrets, backups, the queue topology, HTTPS and proxy headers, what to alert
on, and what is deliberately never logged.

The short version of the parts that lose data or leak credentials if skipped:
`APP_ENV=production`, `JWT_SECRET` from a secret manager, a private storage
bucket, `CORS_ALLOW_ORIGINS` without a wildcard, migrations applied before the
new code serves, and `celery beat` running exactly once.

## Repository layout

```
apps/
  web/            Next.js App Router frontend
  api/            FastAPI HTTP API
  worker/         Celery worker (video generation, TTS, QC)
  render-worker/  FFmpeg render worker
packages/
  backend-core/       Shared Python domain, providers, storage, jobs
  provider-contracts/ Provider capability schemas
  prompts/            Versioned prompt registry
  ui/                 Shared React components
  shared-types/       Generated API types
  config/             Shared TypeScript config
infra/            Docker, nginx, scripts, migrations
docs/             Architecture, ADRs, API, prompts, operations, security
tests/            E2E specs and fixtures
```

## Contributing

- Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `chore:`, `perf:`, `security:`).
- `make verify` must pass before committing.
- Development follows the phase order in
  [the master taskbook](CLAUDE_CODE_AI_PRODUCT_VIDEO_PLATFORM_MASTER_TASKBOOK.md);
  progress is tracked in [`TASK_STATUS.md`](TASK_STATUS.md) and notable
  engineering changes in [`DEVLOG.md`](DEVLOG.md).
- Architectural decisions get an ADR in [`docs/adr/`](docs/adr/).
