"""Versioned API surface, ``/api/v1`` (taskbook §41).

Everything a client consumes lives under a version prefix so a breaking change
ships as ``/api/v2`` alongside the old surface rather than as a silent break.
Liveness and readiness deliberately stay outside it (§70): an orchestrator
probe should not follow API versioning.

Resource routers are added by the phases that build them — auth and workspaces
in PHASE 3, uploads in PHASE 4, products in PHASE 5, and so on.
"""

from aipvs_api.v1.router import api_v1_router

__all__ = ["api_v1_router"]
