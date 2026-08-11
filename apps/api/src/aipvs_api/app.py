"""FastAPI application factory.

A factory rather than a module-level singleton so tests can build an isolated
app per case, and so PHASE 2 can inject settings and middleware (P2-T03
request-id, P2-T04 logging) without import-time side effects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aipvs_api import __version__, health
from backend_core.cache import close_redis
from backend_core.db import dispose_engines

# Taskbook §5.2 makes this OpenAPI document the source of truth for the HTTP
# contract; the web client's types are generated from it in P2-T08.
OPENAPI_DESCRIPTION = """
HTTP API for AI Product Video Studio.

Long-running AI work (analysis, shot generation, TTS, render, QC) is **never**
performed inside a request. Those endpoints validate, create a `GenerationJob`,
enqueue it and return `202 Accepted` with a `job_id` to poll (taskbook §22).
""".strip()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Release pooled resources on shutdown.

    Connections are opened lazily on first use rather than at startup: an API
    that refuses to boot because Redis is briefly unavailable cannot serve
    ``/health``, and readiness already reports degraded dependencies (§70).
    """
    yield
    await dispose_engines()
    await close_redis()


def create_app() -> FastAPI:
    """Build the API application."""
    app = FastAPI(
        title="AI Product Video Studio API",
        description=OPENAPI_DESCRIPTION,
        version=__version__,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Liveness/readiness live at the root, outside the versioned surface, so
    # orchestrator probes survive API version changes (§70).
    app.include_router(health.router)

    # The versioned business surface (`/api/v1`, §41) is mounted in P2-T05.

    return app


app = create_app()
