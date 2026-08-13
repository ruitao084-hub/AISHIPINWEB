"""Job status and generation triggers (§22, §23, §26).

§22's API side: validate, create, reserve, enqueue, return `202` with a job id.
Nothing here waits for a provider — §0.1 rule 13 forbids it, and the whole job
system exists so that it never has to.

§26 asks for polling and explicitly warns against building WebSockets first.
`GET /jobs/{id}` is that endpoint; the frontend polls it every few seconds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import OriginDep, SessionDep, rate_limited, require_permission
from backend_core.domain.enums import AuditAction, JobStatus, JobType, Permission
from backend_core.domain.models import GenerationJob
from backend_core.services.audit import AuditService
from backend_core.services.jobs import JobService
from backend_core.services.shot_generation import ShotGenerationService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["jobs"])


class JobResponse(BaseModel):
    id: uuid.UUID
    job_type: JobType
    status: JobStatus
    provider: str
    model: str | None
    progress: int = Field(
        description=(
            "0-100, advisory. Most providers report coarsely or not at all, and "
            "a smooth bar over work we cannot see would be a lie."
        )
    )
    project_id: uuid.UUID | None
    shot_id: uuid.UUID | None
    result_asset_id: uuid.UUID | None
    retry_count: int
    max_retries: int
    error_code: str | None
    estimated_cost: float
    actual_cost: float | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, job: GenerationJob) -> JobResponse:
        return cls(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            provider=job.provider,
            model=job.model,
            progress=job.progress,
            project_id=job.project_id,
            shot_id=job.shot_id,
            result_asset_id=job.result_asset_id,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            # `error_message` is deliberately absent — it can carry a provider's
            # verbatim complaint about a customer's own prompt (§62). The code
            # is enough to act on.
            error_code=job.error_code,
            estimated_cost=job.estimated_cost,
            actual_cost=job.actual_cost,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
        )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Job status",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_job(workspace_id: uuid.UUID, job_id: uuid.UUID, session: SessionDep) -> JobResponse:
    """§26's polling endpoint. The frontend calls this every few seconds."""
    job = await JobService(session).get(workspace_id=workspace_id, job_id=job_id)
    return JobResponse.of(job)


@router.get(
    "/projects/{project_id}/jobs",
    response_model=list[JobResponse],
    summary="A project's jobs",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_project_jobs(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    session: SessionDep,
    job_type: JobType | None = None,
) -> list[JobResponse]:
    jobs = await JobService(session).list_for_project(
        workspace_id=workspace_id, project_id=project_id, job_type=job_type
    )
    return [JobResponse.of(job) for job in jobs]


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
    summary="Cancel a job",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("generate")],
)
async def cancel_job(
    workspace_id: uuid.UUID, job_id: uuid.UUID, session: SessionDep, origin: OriginDep
) -> JobResponse:
    """Stop a job.

    A job that already finished is returned unchanged rather than erroring: the
    user wanted it stopped and it is stopped, and raising over a race they
    cannot see would be pedantry.
    """
    job = await JobService(session).cancel(workspace_id=workspace_id, job_id=job_id)
    await AuditService(session).record(
        AuditAction.JOB_CANCEL,
        workspace_id=workspace_id,
        target_type="generation_job",
        target_id=job.id,
        origin=origin,
        context={"job_type": job.job_type.value, "status": job.status.value},
    )
    return JobResponse.of(job)


@router.post(
    "/projects/{project_id}/storyboards/{storyboard_id}/generate",
    response_model=list[JobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate every shot",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("generate")],
)
async def generate_shots(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    storyboard_id: uuid.UUID,
    session: SessionDep,
    origin: OriginDep,
) -> list[JobResponse]:
    """Queue every shot of an approved storyboard (§22).

    `202`, not `200`: nothing has been generated when this returns. The client
    polls each job id.

    Shots that already have a chosen take are skipped — regenerating one is a
    per-shot action (§103 rule 10), not something a bulk queue should decide.
    """
    jobs = await ShotGenerationService(session).queue_storyboard(
        workspace_id=workspace_id, project_id=project_id, storyboard_id=storyboard_id
    )
    await AuditService(session).record(
        AuditAction.GENERATE,
        workspace_id=workspace_id,
        target_type="storyboard",
        target_id=storyboard_id,
        origin=origin,
        context={"queued": len(jobs), "estimated_cost": sum(j.estimated_cost for j in jobs)},
    )
    await session.commit()
    _enqueue_all(workspace_id, jobs)
    return [JobResponse.of(job) for job in jobs]


@router.post(
    "/projects/{project_id}/storyboards/{storyboard_id}/shots/{shot_id}/generate",
    response_model=JobResponse,
    summary="Generate one shot",
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("generate")],
)
async def generate_shot(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    storyboard_id: uuid.UUID,
    shot_id: uuid.UUID,
    response: Response,
    session: SessionDep,
    origin: OriginDep,
) -> JobResponse:
    """§103 rule 10's one-click regeneration, for a single shot.

    Returns `202` for new work and `200` when §23's idempotency returned an
    existing job — the distinction matters to a client that would otherwise
    count one generation as two.
    """
    job, created = await ShotGenerationService(session).queue_shot(
        workspace_id=workspace_id, storyboard_id=storyboard_id, shot_id=shot_id
    )
    if created:
        # Only the real one. Recording an idempotent replay would make the
        # trail say a shot was generated twice when it was generated once.
        await AuditService(session).record(
            AuditAction.GENERATE,
            workspace_id=workspace_id,
            target_type="shot",
            target_id=shot_id,
            origin=origin,
            context={"job_id": str(job.id), "estimated_cost": job.estimated_cost},
        )
    await session.commit()

    if created:
        _enqueue_all(workspace_id, [job])
        response.status_code = status.HTTP_202_ACCEPTED
    else:
        response.status_code = status.HTTP_200_OK

    return JobResponse.of(job)


def _enqueue_all(workspace_id: uuid.UUID, jobs: list[GenerationJob]) -> None:
    """Hand jobs to Celery after the transaction has committed.

    After, not during: enqueueing inside the transaction would let a worker pop
    a job whose row is not yet visible, and it would leave a phantom task in
    the queue if the transaction then rolled back.
    """
    from aipvs_worker.celery_app import generate_video

    for job in jobs:
        generate_video.delay(str(workspace_id), str(job.id))


__all__: list[str] = ["router"]
