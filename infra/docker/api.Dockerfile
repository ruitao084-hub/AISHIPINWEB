# =============================================================================
# API image (§100, P23-T01)
# =============================================================================
# Build from the repository root:
#   docker build -f infra/docker/api.Dockerfile -t aipvs-api .
#
# Multi-stage, and the split is not cosmetic. The builder holds `uv`, a
# compiler toolchain and the whole workspace; the runtime holds a virtualenv
# and the source. That is roughly 700 MB against 250 MB, and every megabyte is
# pulled onto every node on every deploy.
#
# Three things here are security decisions rather than convention:
#
#   - **No secrets in any layer.** Nothing is `ARG`-ed in and no `.env` is
#     copied. Images are cached, shared and pushed to registries; a secret in
#     a layer is readable by anyone who can pull it, and stays readable after
#     it is rotated (§7, project rule 11).
#   - **Runs as a non-root user.** A container escape from root is a host
#     compromise; from `app` it is a container compromise.
#   - **`--frozen`.** The lockfile is installed exactly, never re-resolved. A
#     build that quietly picked a newer transitive dependency would produce an
#     image nobody tested.
# =============================================================================

FROM python:3.11-slim-bookworm AS builder

# `uv` is pinned by digest-bearing tag rather than `latest`: a build that
# resolves its own installer is not reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# Manifests first, source second. Dependencies change far less often than
# code, so this layer is a cache hit on almost every build — the difference
# between a 20-second image and a three-minute one.
COPY pyproject.toml uv.lock ./
COPY packages/backend-core/pyproject.toml packages/backend-core/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY apps/render-worker/pyproject.toml apps/render-worker/

# `--no-install-workspace` installs only third-party dependencies. The
# workspace packages are copied next and installed after, so editing our own
# code never invalidates the dependency layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

COPY packages/ packages/
COPY apps/ apps/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.11-slim-bookworm AS runtime

# `ffprobe` only — the API probes uploaded media (§4.7) but never encodes.
# `ffmpeg` proper lives in the render image, where it is 100 MB well spent and
# here would be 100 MB of attack surface for a binary nothing calls.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /build/.venv /app/.venv
COPY --from=builder --chown=app:app /build/packages /app/packages
COPY --from=builder --chown=app:app /build/apps /app/apps
COPY --chown=app:app infra/migrations /app/infra/migrations
COPY --chown=app:app alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production

USER app
EXPOSE 8000

# Against `/health`, not `/ready`. Liveness asks "is this process serving"; a
# readiness probe that fails when Redis blips would have the orchestrator
# restart a perfectly healthy API (§70).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# `--proxy-headers` because TLS terminates upstream; without it every request
# looks like plain HTTP and the HSTS header is never sent. Workers are set by
# the orchestrator's replica count instead of here — two levels of process
# multiplication is how a 2-core node ends up running 16 workers.
CMD ["uvicorn", "aipvs_api.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header"]
