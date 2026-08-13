"""Project, creative plan and script endpoints (§10.9-§10.11, §16, §17).

The two generation endpoints require `GENERATION_RUN` rather than
`PROJECT_WRITE`. §40 deliberately does not let write access imply spending the
workspace's money, and both of these call a paid model.

`POST .../scripts/{id}/approve` is the one that carries weight downstream: it
supersedes every other version and is what PHASE 8 reads. It is a distinct
action rather than a status a client could PATCH, for the same reason fact
verification is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import CurrentUser, SessionDep, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    AspectRatio,
    Permission,
    ProjectPurpose,
    ProjectStatus,
    QualityMode,
    ScriptStatus,
    TargetPlatform,
    VideoStyle,
)
from backend_core.domain.models import CreativePlan, Project, Script
from backend_core.providers.creative_schemas import ScriptDocument
from backend_core.services.creative import CreativeService
from backend_core.services.projects import ProjectService

router = APIRouter(prefix="/workspaces/{workspace_id}/projects", tags=["projects"])


# --- responses -------------------------------------------------------------


class ProjectResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    purpose: ProjectPurpose
    target_platform: TargetPlatform
    target_audience: str | None
    language: str
    aspect_ratio: AspectRatio
    duration_seconds: int
    style: VideoStyle
    quality_mode: QualityMode
    status: ProjectStatus
    failure_reason: str | None
    created_at: datetime

    @classmethod
    def of(cls, project: Project) -> ProjectResponse:
        return cls(
            id=project.id,
            product_id=project.product_id,
            name=project.name,
            purpose=project.purpose,
            target_platform=project.target_platform,
            target_audience=project.target_audience,
            language=project.language,
            aspect_ratio=project.aspect_ratio,
            duration_seconds=project.duration_seconds,
            style=project.style,
            quality_mode=project.quality_mode,
            status=project.status,
            failure_reason=project.failure_reason,
            created_at=project.created_at,
        )


class CreativePlanResponse(BaseModel):
    id: uuid.UUID
    version: int
    title: str
    concept: str
    hook: str
    core_message: str
    narrative_structure: str
    visual_direction: str
    camera_direction: str
    music_direction: str
    ending_cta: str
    risk_notes: str = Field(
        description=(
            "Where the model flagged something a human should check before filming — "
            "a claim it wanted and did not have, or a direction resting on something "
            "unstated (§16)."
        )
    )
    selected: bool
    created_at: datetime

    @classmethod
    def of(cls, plan: CreativePlan) -> CreativePlanResponse:
        return cls(
            id=plan.id,
            version=plan.version,
            title=plan.title,
            concept=plan.concept,
            hook=plan.hook,
            core_message=plan.core_message,
            narrative_structure=plan.narrative_structure,
            visual_direction=plan.visual_direction,
            camera_direction=plan.camera_direction,
            music_direction=plan.music_direction,
            ending_cta=plan.ending_cta,
            risk_notes=plan.risk_notes,
            selected=plan.selected,
            created_at=plan.created_at,
        )


class ScriptResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: ScriptStatus
    creative_plan_id: uuid.UUID | None
    content_json: dict[str, Any]
    plain_text: str
    sourced_claim_ids: list[uuid.UUID] = Field(
        description=(
            "The VERIFIED claims that were in scope when this was written (P7-T09). "
            "A claim withdrawn later can be traced to every script that used it."
        )
    )
    estimated_duration_seconds: float | None
    created_at: datetime

    @classmethod
    def of(cls, script: Script) -> ScriptResponse:
        return cls(
            id=script.id,
            version=script.version,
            status=script.status,
            creative_plan_id=script.creative_plan_id,
            content_json=script.content_json,
            plain_text=script.plain_text,
            sourced_claim_ids=[uuid.UUID(value) for value in script.sourced_claim_ids],
            estimated_duration_seconds=script.estimated_duration_seconds,
            created_at=script.created_at,
        )


# --- requests --------------------------------------------------------------


class CreateProjectRequest(ApiRequest):
    product_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    purpose: ProjectPurpose = ProjectPurpose.SOCIAL_AD
    target_platform: TargetPlatform = TargetPlatform.DOUYIN
    target_audience: str | None = None
    language: str = Field(default="zh-CN", max_length=16)
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT_9_16
    # Bounded here as well as in the database: a client should learn its
    # request was unreasonable from a 422 naming the field, not from a
    # constraint violation.
    duration_seconds: int = Field(default=30, ge=5, le=600)
    style: VideoStyle = VideoStyle.CLEAN_MINIMAL
    quality_mode: QualityMode = QualityMode.STANDARD


class UpdateProjectRequest(ApiRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_audience: str | None = None
    duration_seconds: int | None = Field(default=None, ge=5, le=600)
    aspect_ratio: AspectRatio | None = None
    style: VideoStyle | None = None
    quality_mode: QualityMode | None = None


class ReviseScriptRequest(ApiRequest):
    """A human's edit. Validated against §17's schema like any generated one —
    a hand-written script with eight sections would break PHASE 8 exactly as a
    generated one would."""

    document: ScriptDocument


# --- project routes --------------------------------------------------------


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a project",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def create_project(
    workspace_id: uuid.UUID,
    payload: CreateProjectRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ProjectResponse:
    """Create a project against a product. Always starts in DRAFT (§105)."""
    project = await ProjectService(session).create(
        workspace_id=workspace_id,
        user=user,
        product_id=payload.product_id,
        name=payload.name,
        purpose=payload.purpose,
        target_platform=payload.target_platform,
        target_audience=payload.target_audience,
        language=payload.language,
        aspect_ratio=payload.aspect_ratio,
        duration_seconds=payload.duration_seconds,
        style=payload.style,
        quality_mode=payload.quality_mode,
    )
    return ProjectResponse.of(project)


@router.get(
    "",
    response_model=list[ProjectResponse],
    summary="List projects",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_projects(
    workspace_id: uuid.UUID,
    session: SessionDep,
    product_id: uuid.UUID | None = None,
    project_status: ProjectStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectResponse]:
    projects = await ProjectService(session).list_projects(
        workspace_id=workspace_id,
        product_id=product_id,
        status=project_status,
        limit=limit,
        offset=offset,
    )
    return [ProjectResponse.of(project) for project in projects]


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get a project",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_project(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> ProjectResponse:
    project = await ProjectService(session).get(workspace_id=workspace_id, project_id=project_id)
    return ProjectResponse.of(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Edit the brief",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def update_project(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: UpdateProjectRequest,
    session: SessionDep,
) -> ProjectResponse:
    """Change the brief, while the project is still ahead of generation.

    Refused afterwards: changing the duration once shots exist would silently
    invalidate work already paid for. Move the project back a stage first.
    """
    project = await ProjectService(session).update(
        workspace_id=workspace_id,
        project_id=project_id,
        name=payload.name,
        target_audience=payload.target_audience,
        duration_seconds=payload.duration_seconds,
        aspect_ratio=payload.aspect_ratio,
        style=payload.style,
        quality_mode=payload.quality_mode,
    )
    return ProjectResponse.of(project)


@router.post(
    "/{project_id}/archive",
    response_model=ProjectResponse,
    summary="Archive a project",
    dependencies=[require_permission(Permission.PROJECT_DELETE)],
)
async def archive_project(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> ProjectResponse:
    project = await ProjectService(session).archive(
        workspace_id=workspace_id, project_id=project_id
    )
    return ProjectResponse.of(project)


# --- creative plans (§16) --------------------------------------------------


@router.post(
    "/{project_id}/creative-plans",
    response_model=list[CreativePlanResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate three creative directions",
    # Costs money (§40).
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def generate_creative_plans(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> list[CreativePlanResponse]:
    """Produce three directions to choose between (§16).

    Built only from the product's VERIFIED facts and claims — a product with
    nothing confirmed is refused with `CLAIM_NOT_VERIFIED` rather than served
    with invented content.

    Re-running produces a new version and clears any previous selection; the
    old plans stay readable (§103 rule 9).
    """
    plans = await CreativeService(session).generate_plans(
        workspace_id=workspace_id, project_id=project_id
    )
    return [CreativePlanResponse.of(plan) for plan in plans]


@router.get(
    "/{project_id}/creative-plans",
    response_model=list[CreativePlanResponse],
    summary="List creative plans",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_creative_plans(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> list[CreativePlanResponse]:
    plans = await CreativeService(session).list_plans(
        workspace_id=workspace_id, project_id=project_id
    )
    return [CreativePlanResponse.of(plan) for plan in plans]


@router.post(
    "/{project_id}/creative-plans/{plan_id}/select",
    response_model=CreativePlanResponse,
    summary="Choose a direction",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def select_creative_plan(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    plan_id: uuid.UUID,
    session: SessionDep,
) -> CreativePlanResponse:
    """Pick the plan to script from (§16). Exactly one per project."""
    plan = await CreativeService(session).select_plan(
        workspace_id=workspace_id, project_id=project_id, plan_id=plan_id
    )
    return CreativePlanResponse.of(plan)


# --- scripts (§17) ---------------------------------------------------------


@router.post(
    "/{project_id}/scripts",
    response_model=ScriptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a script",
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def generate_script(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> ScriptResponse:
    """Write a script from the selected plan (§17).

    Only VERIFIED claims reach the generator (P7-T09), and which ones did is
    recorded on the row. Always a new version; the previous ones stay readable.
    """
    script = await CreativeService(session).generate_script(
        workspace_id=workspace_id, project_id=project_id
    )
    return ScriptResponse.of(script)


@router.get(
    "/{project_id}/scripts",
    response_model=list[ScriptResponse],
    summary="Script history",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_scripts(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> list[ScriptResponse]:
    scripts = await CreativeService(session).list_scripts(
        workspace_id=workspace_id, project_id=project_id
    )
    return [ScriptResponse.of(script) for script in scripts]


@router.post(
    "/{project_id}/scripts/revise",
    response_model=ScriptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save an edited script",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def revise_script(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: ReviseScriptRequest,
    session: SessionDep,
) -> ScriptResponse:
    """Store a human's edit as a new version (§17).

    Never an update in place — history survives an edit, so a user who regrets
    a change can still see what they had.
    """
    script = await CreativeService(session).revise_script(
        workspace_id=workspace_id, project_id=project_id, document=payload.document
    )
    return ScriptResponse.of(script)


@router.post(
    "/{project_id}/scripts/{script_id}/approve",
    response_model=ScriptResponse,
    summary="Approve a script",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def approve_script(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    script_id: uuid.UUID,
    session: SessionDep,
) -> ScriptResponse:
    """Accept this version and supersede the rest.

    PHASE 8 reads the approved script, so exactly one may hold that status —
    enforced by a partial unique index, not only by this handler.
    """
    script = await CreativeService(session).approve_script(
        workspace_id=workspace_id, project_id=project_id, script_id=script_id
    )
    return ScriptResponse.of(script)
