"""Timeline editing endpoints (§97, PHASE 20).

§97's acceptance is that a user can adjust the cut *without regenerating AI
shots*, and the shape of this surface is what makes that true: edits are
operations on a timeline, and the only thing that spends money here is the
re-render — which is an encode, not a generation.

Edits are a POST of operations rather than a PUT of a timeline. A client that
could send a whole timeline could send one referencing another workspace's
object key, or a clip length nobody generated. Operations name what may change;
everything else is not expressible.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import SessionDep, rate_limited, require_permission
from aipvs_api.v1.jobs import JobResponse
from aipvs_api.v1.renders import RenderResponse
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import Permission
from backend_core.render.timeline import Timeline
from backend_core.services.editor import (
    ReorderShots,
    SetLogo,
    SetSubtitleStyle,
    SetTrackGain,
    TimelineEditor,
    TrimClip,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/projects/{project_id}/timeline",
    tags=["editor"],
)


class TimelineItemResponse(BaseModel):
    start_ms: int
    end_ms: int
    duration_ms: int
    asset_id: str | None
    object_key: str | None
    source_start_ms: int
    text: str
    gain: float
    metadata: dict[str, Any]


class TrackResponse(BaseModel):
    type: str
    items: list[TimelineItemResponse]


class TimelineResponse(BaseModel):
    """The editable cut (P20-T01)."""

    project_id: uuid.UUID
    width: int
    height: int
    fps: int
    duration_ms: int
    edited: bool = Field(description="True once operations have been applied to the composed cut.")
    tracks: list[TrackResponse]

    @classmethod
    def of(cls, project_id: uuid.UUID, timeline: Timeline, *, edited: bool) -> TimelineResponse:
        return cls(
            project_id=project_id,
            width=timeline.canvas.width,
            height=timeline.canvas.height,
            fps=timeline.canvas.fps,
            duration_ms=timeline.duration_ms,
            edited=edited,
            tracks=[
                TrackResponse(
                    type=track.type.value,
                    items=[
                        TimelineItemResponse(
                            start_ms=item.start_ms,
                            end_ms=item.end_ms,
                            duration_ms=item.duration_ms,
                            asset_id=item.asset_id,
                            object_key=item.object_key,
                            source_start_ms=item.source_start_ms,
                            text=item.text,
                            gain=item.gain,
                            metadata=dict(item.metadata),
                        )
                        for item in sorted(track.items, key=lambda entry: entry.start_ms)
                    ],
                )
                for track in timeline.tracks
            ],
        )


#: The operations, discriminated by `op` so a client sends one array and the
#: server knows which shape each entry is. Tagged rather than inferred: a union
#: matched by field presence would accept a reorder missing its order and read
#: it as something else.
AnyOperation = Annotated[
    ReorderShots | TrimClip | SetTrackGain | SetSubtitleStyle | SetLogo,
    Field(discriminator="op"),
]


class EditRequest(ApiRequest):
    operations: list[AnyOperation] = Field(min_length=1, max_length=64)


class RerenderRequest(ApiRequest):
    """Nothing to configure yet. Present so adding a field is not a breaking
    change from a body-less POST."""

    confirm: Literal[True] = True


class RerenderResponse(BaseModel):
    render: RenderResponse
    job: JobResponse


@router.get(
    "",
    response_model=TimelineResponse,
    summary="The editable timeline",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_timeline(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> TimelineResponse:
    """§33's document, as the editor sees it (P20-T01).

    Composes one from the shots on first open and returns the stored one
    afterwards — rebuilding every time would discard every edit the moment
    somebody reopened the page.
    """
    draft = await TimelineEditor(session).draft(workspace_id=workspace_id, project_id=project_id)
    return TimelineResponse.of(project_id, draft.timeline, edited=draft.edited)


@router.post(
    "/edits",
    response_model=TimelineResponse,
    summary="Reorder, trim, adjust volume, style subtitles, set a logo",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def apply_edits(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: EditRequest,
    session: SessionDep,
) -> TimelineResponse:
    """Apply P20-T02 through T07 in one batch.

    Batched because the operations interact: a reorder followed by a trim by
    index means something different from the reverse, and two round trips would
    let a second client interleave between them.

    Costs nothing. No shot is touched, which is the whole of §97.
    """
    draft = await TimelineEditor(session).apply(
        workspace_id=workspace_id,
        project_id=project_id,
        operations=list(payload.operations),
    )
    return TimelineResponse.of(project_id, draft.timeline, edited=draft.edited)


@router.post(
    "/render",
    response_model=RerenderResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-encode the edited cut",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("render")],
)
async def rerender(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: RerenderRequest,
    session: SessionDep,
) -> RerenderResponse:
    """§97's point (P20-T08): encode the edit, generate nothing.

    Queues a render job and no generation job. The clips already exist; only
    the composition changed.
    """
    _ = payload
    render, job, created = await TimelineEditor(session).rerender(
        workspace_id=workspace_id, project_id=project_id
    )
    await session.commit()

    if created:
        from aipvs_worker.celery_app import compose_render

        compose_render.delay(str(workspace_id), str(job.id))

    return RerenderResponse(render=RenderResponse.of(render), job=JobResponse.of(job))


__all__: list[str] = ["router"]
