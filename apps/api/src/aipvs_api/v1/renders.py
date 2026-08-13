"""Voice, render, QC and download endpoints (§30, §34, §37, PHASE 15).

The half of the pipeline that turns approved shots into a file someone can
download. Every write here is `202` and a job id — §0.1 rule 13 forbids an HTTP
request waiting on an encode, and a render is minutes of CPU.

Downloads are presigned URLs rather than bytes proxied through the API (§27,
§60). Streaming a 40 MB file through a request worker holds it for the whole
download; a signed URL hands the client straight to storage and expires on its
own.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import OriginDep, SessionDep, rate_limited, require_permission
from aipvs_api.v1.jobs import JobResponse
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    AuditAction,
    Permission,
    QCCheckType,
    QCStatus,
    RenderStatus,
)
from backend_core.domain.models import GenerationJob, QualityCheck, Render, VoiceoverTrack
from backend_core.services.audit import AuditService
from backend_core.services.post_production import PostProductionService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/projects/{project_id}",
    tags=["renders"],
)


# --- responses -------------------------------------------------------------


class VoiceoverResponse(BaseModel):
    id: uuid.UUID
    language: str
    voice: str
    provider: str
    audio_asset_id: uuid.UUID | None
    total_duration_ms: int
    segments: list[dict[str, Any]]
    created_at: datetime

    @classmethod
    def of(cls, track: VoiceoverTrack) -> VoiceoverResponse:
        return cls(
            id=track.id,
            language=track.language,
            voice=track.voice,
            provider=track.provider,
            audio_asset_id=track.audio_asset_id,
            total_duration_ms=track.total_duration_ms,
            segments=list(track.segments),
            created_at=track.created_at,
        )


class RenderResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    storyboard_id: uuid.UUID | None
    version: int
    status: RenderStatus
    output_asset_id: uuid.UUID | None
    thumbnail_asset_id: uuid.UUID | None
    duration_ms: int | None
    width: int | None
    height: int | None
    error_message: str | None
    created_at: datetime

    @classmethod
    def of(cls, render: Render) -> RenderResponse:
        return cls(
            id=render.id,
            project_id=render.project_id,
            storyboard_id=render.storyboard_id,
            version=render.version,
            status=render.status,
            output_asset_id=render.output_asset_id,
            thumbnail_asset_id=render.thumbnail_asset_id,
            duration_ms=render.duration_ms,
            width=render.width,
            height=render.height,
            error_message=render.error_message,
            created_at=render.created_at,
        )


class RenderStartedResponse(BaseModel):
    """What a composition request returns: the render row and the job to poll."""

    render: RenderResponse
    job: JobResponse


class QualityCheckResponse(BaseModel):
    id: uuid.UUID
    render_id: uuid.UUID | None
    shot_id: uuid.UUID | None
    check_type: QCCheckType
    status: QCStatus
    findings: list[dict[str, Any]]
    created_at: datetime

    @classmethod
    def of(cls, check: QualityCheck) -> QualityCheckResponse:
        return cls(
            id=check.id,
            render_id=check.render_id,
            shot_id=check.shot_id,
            check_type=check.check_type,
            status=check.status,
            findings=list(check.findings),
            created_at=check.created_at,
        )


class DownloadResponse(BaseModel):
    """A time-limited link to the finished video (§60)."""

    url: str = Field(description="Presigned URL. Expires; do not store it.")
    expires_in_seconds: int
    filename: str
    size_bytes: int | None
    duration_ms: int | None


class RenderRequest(ApiRequest):
    burn_subtitles: bool = Field(
        default=True,
        description="Burn the subtitle track into the picture (§31).",
    )


# --- voice (§30) -----------------------------------------------------------


@router.post(
    "/voiceover",
    response_model=JobResponse,
    summary="Synthesise the narration",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("generate")],
)
async def create_voiceover(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    response: Response,
    session: SessionDep,
) -> JobResponse:
    """Queue TTS for the approved storyboard (§30).

    `202` for new work, `200` when §23's idempotency returned the existing job
    — the same distinction the shot generation endpoints draw, for the same
    reason: a client that cannot tell counts one synthesis as two.
    """
    job, created = await PostProductionService(session).queue_voiceover(
        workspace_id=workspace_id, project_id=project_id
    )
    await session.commit()

    if created:
        _enqueue(workspace_id, job)
        response.status_code = status.HTTP_202_ACCEPTED
    else:
        response.status_code = status.HTTP_200_OK
    return JobResponse.of(job)


@router.get(
    "/voiceover",
    response_model=VoiceoverResponse,
    summary="The project's narration track",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_voiceover(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> VoiceoverResponse:
    track = await PostProductionService(session).latest_voiceover(
        workspace_id=workspace_id, project_id=project_id
    )
    if track is None:
        from backend_core.errors import NotFoundError

        raise NotFoundError("This project has no narration yet.")
    return VoiceoverResponse.of(track)


# --- render (§33, §34) -----------------------------------------------------


@router.post(
    "/renders",
    response_model=RenderStartedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Compose the final video",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("render")],
)
async def create_render(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: RenderRequest,
    session: SessionDep,
    origin: OriginDep,
) -> RenderStartedResponse:
    """Build the timeline and queue the composition (§33, §34).

    Always `202`: even when §23 returns an existing job, a `Render` row was
    created for this request and the client has something new to poll.
    """
    render, job, created = await PostProductionService(session).create_render(
        workspace_id=workspace_id, project_id=project_id, burn_subtitles=payload.burn_subtitles
    )
    await AuditService(session).record(
        AuditAction.RENDER,
        workspace_id=workspace_id,
        target_type="render",
        target_id=render.id,
        origin=origin,
        context={"project_id": str(project_id), "version": render.version},
    )
    await session.commit()

    if created:
        _enqueue(workspace_id, job)
    return RenderStartedResponse(render=RenderResponse.of(render), job=JobResponse.of(job))


@router.get(
    "/renders",
    response_model=list[RenderResponse],
    summary="Every render of this project",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_renders(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> list[RenderResponse]:
    renders = await PostProductionService(session).list_renders(
        workspace_id=workspace_id, project_id=project_id
    )
    return [RenderResponse.of(render) for render in renders]


@router.get(
    "/renders/{render_id}",
    response_model=RenderResponse,
    summary="One render",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_render(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    render_id: uuid.UUID,
    session: SessionDep,
) -> RenderResponse:
    render = await PostProductionService(session).get_render(
        workspace_id=workspace_id, project_id=project_id, render_id=render_id
    )
    return RenderResponse.of(render)


# --- quality checks (§37) --------------------------------------------------


@router.post(
    "/renders/{render_id}/quality-checks",
    response_model=JobResponse,
    summary="Run quality checks",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("render")],
)
async def create_quality_check(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    render_id: uuid.UUID,
    response: Response,
    session: SessionDep,
) -> JobResponse:
    job, created = await PostProductionService(session).queue_quality_check(
        workspace_id=workspace_id, project_id=project_id, render_id=render_id
    )
    await session.commit()

    if created:
        _enqueue(workspace_id, job)
        response.status_code = status.HTTP_202_ACCEPTED
    else:
        response.status_code = status.HTTP_200_OK
    return JobResponse.of(job)


@router.get(
    "/quality-checks",
    response_model=list[QualityCheckResponse],
    summary="Quality check results",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_quality_checks(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    session: SessionDep,
    render_id: uuid.UUID | None = None,
) -> list[QualityCheckResponse]:
    checks = await PostProductionService(session).list_quality_checks(
        workspace_id=workspace_id, project_id=project_id, render_id=render_id
    )
    return [QualityCheckResponse.of(check) for check in checks]


# --- download (§60) --------------------------------------------------------


@router.get(
    "/download",
    response_model=DownloadResponse,
    summary="Download the finished video",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def download(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    session: SessionDep,
    origin: OriginDep,
    render_id: uuid.UUID | None = None,
) -> DownloadResponse:
    """A presigned link to the newest finished render, or a named one.

    The link is short-lived and the API does not store it. Handing back a
    permanent public URL would make every finished video readable by anyone who
    ever saw one link (§60).
    """
    from backend_core.config import get_settings
    from backend_core.storage.s3 import get_storage

    asset = await PostProductionService(session).download_target(
        workspace_id=workspace_id, project_id=project_id, render_id=render_id
    )
    expires_in = get_settings().s3_signed_url_ttl_seconds
    url = get_storage().presigned_download_url(asset.object_key, expires_in=expires_in)

    # §60 requires downloads to be recorded. The *link* is not: it carries a
    # signature that grants the access, and an audit table holding live
    # credentials would be a worse leak than the one it exists to detect.
    await AuditService(session).record(
        AuditAction.DOWNLOAD,
        workspace_id=workspace_id,
        target_type="media_asset",
        target_id=asset.id,
        origin=origin,
        context={"project_id": str(project_id), "size_bytes": asset.size_bytes},
    )

    return DownloadResponse(
        url=url,
        expires_in_seconds=expires_in,
        filename=asset.original_filename or "video.mp4",
        size_bytes=asset.size_bytes,
        duration_ms=asset.duration_ms,
    )


def _enqueue(workspace_id: uuid.UUID, job: GenerationJob) -> None:
    """Hand a job to its queue after the transaction has committed (§25).

    After, not during: a worker popping a job whose row is not yet visible
    fails on a lookup, and a rolled-back transaction would leave a phantom task
    behind.
    """
    from aipvs_worker.celery_app import compose_render, run_quality_check, synthesize_speech
    from backend_core.domain.enums import JobType

    task = {
        JobType.TTS: synthesize_speech,
        JobType.RENDER: compose_render,
        JobType.QC: run_quality_check,
    }.get(job.job_type)
    if task is None:  # pragma: no cover — every job type this module creates is above
        raise ValueError(f"No queue for job type {job.job_type.value}.")
    task.delay(str(workspace_id), str(job.id))


__all__: list[str] = ["router"]
