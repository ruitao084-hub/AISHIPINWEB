"""Liveness and readiness endpoints (taskbook §70).

The split matters for orchestrators:

* ``/health`` is **liveness** — the process is up and serving. It must never
  depend on Postgres, Redis or S3, otherwise a transient dependency blip gets
  the container killed and restarted instead of merely taken out of rotation.
* ``/ready`` is **readiness** — the process can serve real traffic, which does
  depend on its backing services.

PHASE 0 has no backing services yet, so ``/ready`` reports an empty dependency
set. P1-T04/T06/T07 register the real Postgres, Redis and S3 probes here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

router = APIRouter(tags=["health"])


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


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies are not ready."}},
)
async def ready(response: Response) -> ReadyResponse:
    """Report whether every backing service is usable.

    Returns 503 when any dependency fails so load balancers stop routing here
    rather than serving errors to users.
    """
    # P1-T04/T06/T07 append Postgres, Redis and S3 probes to this list.
    dependencies: list[DependencyStatus] = []

    all_ready = all(dependency.ready for dependency in dependencies)
    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(ready=all_ready, dependencies=dependencies)
