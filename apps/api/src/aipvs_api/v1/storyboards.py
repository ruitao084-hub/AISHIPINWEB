"""Storyboard and shot endpoints (§10.12-§10.14, §18, §19, §29).

One thing is deliberately absent from this surface: **there is no way to set a
shot's `visual_prompt` or `negative_prompt`.** §19 forbids handing a video
model a sentence a user typed, so those are compiler outputs. A user edits the
lighting, the camera, the composition — the prompt is rebuilt from them. Making
the prompt directly writable would put the whole of §19 one PATCH away from
being bypassed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import SessionDep, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    Permission,
    ReferenceRole,
    ShotStatus,
    ShotType,
    StoryboardStatus,
    TransitionType,
)
from backend_core.domain.models import Shot, ShotReference, Storyboard
from backend_core.services.storyboards import StoryboardService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/projects/{project_id}/storyboards",
    tags=["storyboards"],
)


# --- responses -------------------------------------------------------------


class ShotReferenceResponse(BaseModel):
    id: uuid.UUID
    media_asset_id: uuid.UUID
    reference_role: ReferenceRole
    weight: float | None

    @classmethod
    def of(cls, reference: ShotReference) -> ShotReferenceResponse:
        return cls(
            id=reference.id,
            media_asset_id=reference.media_asset_id,
            reference_role=reference.reference_role,
            weight=reference.weight,
        )


class ShotResponse(BaseModel):
    id: uuid.UUID
    sequence_no: int
    title: str
    shot_type: ShotType
    duration_seconds: float
    description: str
    visual_prompt: str = Field(
        description=(
            "Compiled by the prompt compiler from this shot's fields (§19). "
            "Read-only: edit the fields and it is rebuilt."
        )
    )
    negative_prompt: str
    camera: str
    motion: str
    lighting: str
    composition: str
    voiceover_text: str
    subtitle_text: str
    transition_in: TransitionType
    transition_out: TransitionType
    status: ShotStatus
    identity_lock: bool = Field(
        description=(
            "§29's product identity lock. When on, the compiler adds the "
            "consistency rules and QC checks generated frames against the "
            "identity references below."
        )
    )
    references: list[ShotReferenceResponse]

    @classmethod
    def of(cls, shot: Shot) -> ShotResponse:
        return cls(
            id=shot.id,
            sequence_no=shot.sequence_no,
            title=shot.title,
            shot_type=shot.shot_type,
            duration_seconds=shot.duration_seconds,
            description=shot.description,
            visual_prompt=shot.visual_prompt,
            negative_prompt=shot.negative_prompt,
            camera=shot.camera,
            motion=shot.motion,
            lighting=shot.lighting,
            composition=shot.composition,
            voiceover_text=shot.voiceover_text,
            subtitle_text=shot.subtitle_text,
            transition_in=shot.transition_in,
            transition_out=shot.transition_out,
            status=shot.status,
            identity_lock=shot.identity_lock,
            references=[ShotReferenceResponse.of(item) for item in shot.references],
        )


class StoryboardResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: StoryboardStatus
    script_id: uuid.UUID | None
    total_duration_seconds: float
    model_info: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, storyboard: Storyboard) -> StoryboardResponse:
        return cls(
            id=storyboard.id,
            version=storyboard.version,
            status=storyboard.status,
            script_id=storyboard.script_id,
            total_duration_seconds=storyboard.total_duration_seconds,
            model_info=storyboard.model_info,
            created_at=storyboard.created_at,
        )


# --- requests --------------------------------------------------------------


class UpdateShotRequest(ApiRequest):
    """Everything a user may change about a shot.

    Note the absence of `visual_prompt` and `negative_prompt` — see the module
    docstring. Their absence is the enforcement.
    """

    title: str | None = Field(default=None, max_length=200)
    shot_type: ShotType | None = None
    duration_seconds: float | None = Field(default=None, ge=2, le=10)
    description: str | None = None
    camera: str | None = None
    motion: str | None = None
    lighting: str | None = None
    composition: str | None = None
    voiceover_text: str | None = None
    subtitle_text: str | None = None
    transition_in: TransitionType | None = None
    transition_out: TransitionType | None = None
    identity_lock: bool | None = None


# --- routes ----------------------------------------------------------------


@router.post(
    "",
    response_model=StoryboardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Break the script into shots",
    # Costs money (§40).
    dependencies=[require_permission(Permission.GENERATION_RUN)],
)
async def generate_storyboard(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> StoryboardResponse:
    """Generate a storyboard from the project's approved script (§18).

    Requires an approved script, not merely a latest one — approval is the act
    by which a person accepted the words, and skipping it would make §17's
    review cosmetic.

    Shot durations are scaled to sum to the project's duration, and a
    storyboard that still cannot fit is refused rather than stored.
    """
    storyboard = await StoryboardService(session).generate(
        workspace_id=workspace_id, project_id=project_id
    )
    return StoryboardResponse.of(storyboard)


@router.get(
    "",
    response_model=list[StoryboardResponse],
    summary="Storyboard history",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_storyboards(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> list[StoryboardResponse]:
    storyboards = await StoryboardService(session).list_storyboards(
        workspace_id=workspace_id, project_id=project_id
    )
    return [StoryboardResponse.of(item) for item in storyboards]


@router.get(
    "/{storyboard_id}/shots",
    response_model=list[ShotResponse],
    summary="List a storyboard's shots",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_shots(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    storyboard_id: uuid.UUID,
    session: SessionDep,
) -> list[ShotResponse]:
    service = StoryboardService(session)
    # Resolves tenancy and existence before touching shots, so a shot list can
    # never confirm a storyboard id belongs to another workspace (§60).
    await service.get_storyboard(
        workspace_id=workspace_id, project_id=project_id, storyboard_id=storyboard_id
    )
    shots = await service.list_shots(workspace_id=workspace_id, storyboard_id=storyboard_id)
    return [ShotResponse.of(shot) for shot in shots]


@router.patch(
    "/{storyboard_id}/shots/{shot_id}",
    response_model=ShotResponse,
    summary="Edit a shot",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def update_shot(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    storyboard_id: uuid.UUID,
    shot_id: uuid.UUID,
    payload: UpdateShotRequest,
    session: SessionDep,
) -> ShotResponse:
    """Change a shot's fields; the prompt is recompiled from them (§19).

    Changing the duration recomputes the storyboard's total, which is checked
    again when the storyboard is approved.
    """
    shot = await StoryboardService(session).update_shot(
        workspace_id=workspace_id,
        storyboard_id=storyboard_id,
        shot_id=shot_id,
        title=payload.title,
        shot_type=payload.shot_type,
        duration_seconds=payload.duration_seconds,
        description=payload.description,
        camera=payload.camera,
        motion=payload.motion,
        lighting=payload.lighting,
        composition=payload.composition,
        voiceover_text=payload.voiceover_text,
        subtitle_text=payload.subtitle_text,
        transition_in=payload.transition_in,
        transition_out=payload.transition_out,
        identity_lock=payload.identity_lock,
    )
    return ShotResponse.of(shot)


@router.post(
    "/{storyboard_id}/approve",
    response_model=StoryboardResponse,
    summary="Approve a storyboard",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def approve_storyboard(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    storyboard_id: uuid.UUID,
    session: SessionDep,
) -> StoryboardResponse:
    """Accept a storyboard and supersede the rest.

    Re-checks §18's duration constraint and that every shot carries a compiled
    prompt — this is the last moment before PHASE 9 starts spending money, and
    a shot with no prompt is one the job system has nothing to send for.
    """
    storyboard = await StoryboardService(session).approve(
        workspace_id=workspace_id, project_id=project_id, storyboard_id=storyboard_id
    )
    return StoryboardResponse.of(storyboard)
