# Development Log

Notable engineering changes and the reasoning behind them (taskbook §134).
Task-level progress lives in [`TASK_STATUS.md`](TASK_STATUS.md); formal
decisions live in [`docs/adr/`](docs/adr/). This file records the things that
are useful to know later but too small for an ADR.

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
