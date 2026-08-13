# =============================================================================
# Worker image (§25, §100, P23-T01)
# =============================================================================
# Build from the repository root:
#   docker build -f infra/docker/worker.Dockerfile -t aipvs-worker .
#
# One image, five deployments. §25 gives video, tts, render, qc and default
# their own queues with their own concurrency, but they share a codebase — so
# the queue is a runtime argument, not a build one:
#
#   docker run aipvs-worker celery -A aipvs_worker.celery_app worker -Q render --concurrency=2
#
# Five images would be five things to build, scan and keep in step for no gain.
#
# This image *does* carry ffmpeg, unlike the API's: the render worker encodes
# (§34), and the ~100 MB is the point of the container rather than dead weight.
# =============================================================================

FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

COPY pyproject.toml uv.lock ./
COPY packages/backend-core/pyproject.toml packages/backend-core/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
COPY apps/render-worker/pyproject.toml apps/render-worker/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace

COPY packages/ packages/
COPY apps/ apps/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.11-slim-bookworm AS runtime

# ffmpeg and the CJK font the subtitle burn-in names (§31). Without the font,
# `force_style='FontName=Noto Sans CJK SC'` silently falls back and every
# Chinese subtitle renders as tofu — a failure that only shows up in the
# finished video, after everything has been paid for.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        fonts-noto-cjk \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /build/.venv /app/.venv
COPY --from=builder --chown=app:app /build/packages /app/packages
COPY --from=builder --chown=app:app /build/apps /app/apps

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    # Renders write here. Bounded and on the container's own filesystem, so a
    # leaked working directory dies with the container rather than filling a
    # shared volume (§35 requires isolation and cleanup).
    TMPDIR=/tmp

USER app

# `inspect ping` rather than a port check: a worker serves nothing over HTTP,
# and a process that is running but disconnected from the broker is exactly
# the state a health check exists to catch.
HEALTHCHECK --interval=60s --timeout=15s --start-period=45s --retries=3 \
    CMD celery -A aipvs_worker.celery_app inspect ping -d "celery@$HOSTNAME" || exit 1

# Overridden per deployment with -Q and --concurrency. The default queue is
# the safe one to land on if someone forgets: it carries maintenance tasks,
# not renders.
CMD ["celery", "-A", "aipvs_worker.celery_app", "worker", \
     "-Q", "default", "--concurrency=2", "--loglevel=INFO"]
