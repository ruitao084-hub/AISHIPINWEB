"""FastAPI application factory.

A factory rather than a module-level singleton so tests build an isolated app
per case and nothing configures itself as an import side effect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from aipvs_api import __version__, health
from aipvs_api.errors import register_exception_handlers
from aipvs_api.middleware import RequestContextMiddleware
from aipvs_api.v1 import api_v1_router
from backend_core.cache import close_redis
from backend_core.config import get_settings
from backend_core.db import dispose_engines
from backend_core.observability import configure_logging, get_logger

logger = get_logger(__name__)

# Taskbook §5.2 makes this OpenAPI document the source of truth for the HTTP
# contract; the web client's types are generated from it (P2-T08).
OPENAPI_DESCRIPTION = """
HTTP API for AI Product Video Studio.

Long-running AI work (analysis, shot generation, TTS, render, QC) is **never**
performed inside a request. Those endpoints validate, create a `GenerationJob`,
enqueue it and return `202 Accepted` with a `job_id` to poll (taskbook §22).

Every failure returns the same envelope:

```json
{"error": {"code": "PROJECT_NOT_FOUND", "message": "...", "request_id": "..."}}
```

`code` comes from a closed set (§65) and is the field clients should branch on.
""".strip()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup; release pooled resources on shutdown.

    Connections are opened lazily on first use rather than at startup: an API
    that refuses to boot because Redis is briefly unavailable cannot serve
    ``/health``, and readiness already reports degraded dependencies (§70).
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "api starting",
        extra={"app_env": settings.app_env, "version": __version__},
    )

    yield

    await dispose_engines()
    await close_redis()
    logger.info("api stopped")


def create_app() -> FastAPI:
    """Build the API application."""
    settings = get_settings()

    app = FastAPI(
        title="AI Product Video Studio API",
        description=OPENAPI_DESCRIPTION,
        version=__version__,
        openapi_url="/openapi.json",
        # Interactive docs are a development affordance, not a production
        # surface — they advertise the whole API to anyone who finds them.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        lifespan=lifespan,
    )

    # Order matters: middleware added last runs first, so request context is
    # bound before CORS or any route work, and every log line is correlated.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # Liveness/readiness sit at the root, outside the versioned surface, so
    # orchestrator probes survive API version changes (§70).
    app.include_router(health.router)
    app.include_router(api_v1_router)

    return app


app = create_app()
