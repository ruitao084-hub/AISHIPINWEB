# ===========================================================================
# AI Product Video Studio — root developer commands (taskbook §77 P0-T05)
# ===========================================================================
# Every target works from a clean checkout. `make help` lists them.
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Reproducible builds: never phone home during CI (§68).
export NEXT_TELEMETRY_DISABLED := 1

COMPOSE_FILE := docker-compose.yml
PY_SRC := packages/backend-core/src apps/api/src apps/worker/src apps/render-worker/src

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: install
install: ## Install all JS and Python dependencies
	pnpm install --frozen-lockfile
	uv sync --all-packages

.PHONY: setup
setup: install ## First-time setup: dependencies + .env
	@if [ -f .env ]; then \
		echo ".env already exists; leaving it untouched."; \
	else \
		cp .env.example .env; \
		./infra/scripts/init-local-env.sh .env; \
		echo "Created .env with local development defaults."; \
		echo "It is gitignored. Provider API keys stay blank — mocks need none."; \
	fi

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

.PHONY: dev
dev: ## Run web + API in development (Ctrl-C stops both)
	@echo "web → http://localhost:3000    api → http://localhost:8000/docs"
	@trap 'kill 0' EXIT INT TERM; \
	uv run uvicorn aipvs_api.app:app --host 127.0.0.1 --port 8000 --reload & \
	pnpm --filter @aipvs/web dev & \
	wait

.PHONY: dev-web
dev-web: ## Run only the Next.js web app
	pnpm --filter @aipvs/web dev

.PHONY: dev-api
dev-api: ## Run only the FastAPI API
	uv run uvicorn aipvs_api.app:app --host 127.0.0.1 --port 8000 --reload

# ---------------------------------------------------------------------------
# Quality gates — these are the same commands CI runs (§68)
# ---------------------------------------------------------------------------

.PHONY: lint
lint: lint-web lint-api ## Lint everything

.PHONY: lint-web
lint-web: ## ESLint over the web app
	pnpm run lint

.PHONY: lint-api
lint-api: ## Ruff lint + format check over all Python
	uv run ruff check .
	uv run ruff format --check .

.PHONY: format
format: ## Auto-fix formatting across JS and Python
	pnpm run format
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: typecheck
typecheck: typecheck-web typecheck-api ## Type-check everything

.PHONY: typecheck-web
typecheck-web: ## tsc --noEmit over the web app
	pnpm run typecheck

.PHONY: typecheck-api
typecheck-api: ## mypy --strict over all Python
	uv run mypy $(PY_SRC)

.PHONY: test
test: test-web test-api ## Run all unit tests

.PHONY: test-web
test-web: ## Vitest
	pnpm run test

.PHONY: test-api
test-api: ## Pytest (unit only; integration needs `make infra-up`)
	uv run pytest -m "not integration and not e2e and not provider"

.PHONY: test-integration
test-integration: ## Pytest integration suite (requires running infrastructure)
	uv run pytest -m integration

.PHONY: test-cov
test-cov: ## Pytest with coverage report (§67)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: build
build: ## Production build of every buildable app
	pnpm run build

.PHONY: verify
verify: ## Full local gate — run before every commit (same order as CI)
	@$(MAKE) lint
	@# Build precedes typecheck: Next.js generates the route types
	@# (LayoutProps/PageProps) into .next/types that `tsc --noEmit` needs.
	@$(MAKE) build
	@$(MAKE) typecheck
	@$(MAKE) test
	@echo ""
	@echo "  All gates passed."

# ---------------------------------------------------------------------------
# Local infrastructure (§4.10) — compose stack lands in PHASE 1
# ---------------------------------------------------------------------------

.PHONY: infra-up
infra-up: ## Start Postgres, Redis and MinIO
	@if [ ! -f $(COMPOSE_FILE) ]; then \
		echo "ERROR: $(COMPOSE_FILE) does not exist yet."; \
		echo "The local infrastructure stack is built in PHASE 1 (P1-T01..T03)."; \
		exit 1; \
	fi
	docker compose up -d postgres redis minio
	@echo "Waiting for services to report healthy..."
	docker compose ps

.PHONY: infra-down
infra-down: ## Stop local infrastructure (volumes are preserved)
	@if [ ! -f $(COMPOSE_FILE) ]; then \
		echo "Nothing to stop: $(COMPOSE_FILE) does not exist yet (built in PHASE 1)."; \
		exit 0; \
	fi
	docker compose down

.PHONY: infra-logs
infra-logs: ## Tail infrastructure logs
	docker compose logs -f --tail=100

# ---------------------------------------------------------------------------
# Database migrations (§73) — schema changes only ever happen through Alembic
# ---------------------------------------------------------------------------

.PHONY: migrate
migrate: ## Apply all pending migrations
	uv run alembic upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back one revision
	uv run alembic downgrade -1

.PHONY: migrate-new
migrate-new: ## Autogenerate a revision: make migrate-new m="add products table"
	@if [ -z "$(m)" ]; then echo 'usage: make migrate-new m="description"'; exit 1; fi
	uv run alembic revision --autogenerate -m "$(m)"

.PHONY: migrate-status
migrate-status: ## Show the current revision and pending history
	uv run alembic current
	uv run alembic history --indicate-current

.PHONY: migrate-sql
migrate-sql: ## Print the SQL a migration would run, without applying it
	uv run alembic upgrade head --sql

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build output and caches (keeps dependencies)
	pnpm run clean || true
	rm -rf .turbo .ruff_cache .mypy_cache .pytest_cache coverage.xml .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
