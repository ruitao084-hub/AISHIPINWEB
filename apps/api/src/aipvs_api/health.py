"""Liveness and readiness endpoints (taskbook §70).

The split matters for orchestrators:

* ``/health`` is **liveness** — the process is up and serving. It must never
  depend on Postgres, Redis or S3, otherwise a transient dependency blip gets
  the container killed and restarted instead of merely taken out of rotation.
* ``/ready`` is **readiness** — the process can serve real traffic, which does
  depend on its backing services.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from backend_core.cache import ping_redis
from backend_core.db import ping_database
from backend_core.storage import ping_storage

router = APIRouter(tags=["health"])

# A readiness probe must answer faster than the orchestrator's own timeout, so
# a hung dependency reports "not ready" instead of hanging the probe itself.
_PROBE_TIMEOUT_SECONDS = 3.0


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: Literal["ok"] = "ok"
    service: str = Field(description="Which process answered the probe.")
    version: str = Field(description="Deployed application version.")


class DependencyStatus(BaseModel):
    """Result of a single readiness probe."""

    name: str
    ready: bool
    detail: str | None = None


class ReadyResponse(BaseModel):
    """Readiness payload."""

    ready: bool
    dependencies: list[DependencyStatus] = Field(default_factory=list)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
async def health() -> HealthResponse:
    """Report that the process is alive. Never touches backing services."""
    from aipvs_api import __version__

    return HealthResponse(service="aipvs-api", version=__version__)


async def _probe(name: str, check: Callable[[], Awaitable[bool]]) -> DependencyStatus:
    """Run one dependency check, converting any failure into a status.

    A probe never raises: an exception here would turn a degraded dependency
    into a 500 on the readiness endpoint itself, which tells the orchestrator
    nothing useful. The exception *type* is reported — never its message, which
    can carry connection strings and credentials (§63).
    """
    try:
        ok = await asyncio.wait_for(check(), timeout=_PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return DependencyStatus(name=name, ready=False, detail="probe timed out")
    except Exception as exc:
        return DependencyStatus(name=name, ready=False, detail=type(exc).__name__)
    return DependencyStatus(name=name, ready=ok)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies are not ready."}},
)
async def ready(response: Response) -> ReadyResponse:
    """Report whether every backing service is usable.

    Returns 503 when any dependency fails so load balancers stop routing here
    rather than serving errors to users. Probes run concurrently: three
    sequential timeouts would exceed most orchestrators' probe budget.
    """
    dependencies = list(
        await asyncio.gather(
            _probe("database", ping_database),
            _probe("redis", ping_redis),
            # boto3 is synchronous; keep it off the event loop.
            _probe("storage", lambda: asyncio.to_thread(ping_storage)),
        )
    )

    all_ready = all(dependency.ready for dependency in dependencies)
    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(ready=all_ready, dependencies=dependencies)
