"""The ``/api/v1`` router and its shared response conventions."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aipvs_api.errors import ErrorResponse
from aipvs_api.v1 import auth, uploads, workspaces

# Documented once here rather than repeated per route, so every endpoint's
# OpenAPI entry advertises the same envelope and the generated TypeScript
# client knows failures have one shape (§5.2).
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    401: {"model": ErrorResponse, "description": "Authentication required"},
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Validation failed"},
    500: {"model": ErrorResponse, "description": "Internal error"},
}

api_v1_router = APIRouter(prefix="/api/v1", responses=COMMON_ERROR_RESPONSES)


class ApiInfo(BaseModel):
    """What this API version is and what it can currently do."""

    version: str = Field(description="API surface version.")
    service: str = Field(description="Service name.")
    features: list[str] = Field(
        default_factory=list,
        description="Capabilities enabled by feature flags (taskbook §122).",
    )


@api_v1_router.get("/", response_model=ApiInfo, summary="API version information", tags=["meta"])
async def api_info() -> ApiInfo:
    """Describe this API version.

    Reports which feature flags are on so a client can adapt without probing
    endpoints that are deliberately disabled.
    """
    from backend_core.config import get_settings

    settings = get_settings()
    features: list[str] = []
    if settings.use_mock_providers:
        features.append("mock_providers")
    if settings.enable_real_video_provider:
        features.append("real_video_provider")
    if settings.enable_qc:
        features.append("qc")
    if settings.enable_credits:
        features.append("credits")
    if settings.enable_multi_provider:
        features.append("multi_provider")
    if settings.enable_batch:
        features.append("batch")

    return ApiInfo(version="v1", service="aipvs-api", features=features)


api_v1_router.include_router(auth.router)
api_v1_router.include_router(workspaces.router)
api_v1_router.include_router(uploads.router)

# Further resource routers mount here as their phases land:
#   PHASE 5  products, brand kits
#   PHASE 7  projects, creative plans, scripts
#   PHASE 8  storyboards, shots
#   PHASE 9  jobs
#   PHASE 12 voice, subtitles
#   PHASE 13 timelines, renders
#   PHASE 14 quality checks
