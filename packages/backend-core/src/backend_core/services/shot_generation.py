"""Queue shot generation (§22, PHASE 11).

The API side of §22's flow, for the one job type that matters most: turning an
approved storyboard's shots into clips.

The idempotency key is derived rather than accepted from the client, and that
is the interesting decision. §23 wants a repeated request to return the
original job; a client-chosen key does that only if the client remembers to
send one, and a double-clicked "Generate" button usually does not. Deriving it
from `(shot_id, prompt)` means the *same shot with the same prompt* is one job
no matter how many times it is asked for — and editing the prompt correctly
produces a new one, because the take will be different.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import JobType, QualityMode, ShotStatus, StoryboardStatus
from backend_core.domain.models import GenerationJob, Shot
from backend_core.errors import ValidationError
from backend_core.observability import get_logger
from backend_core.providers.video import get_video_provider
from backend_core.repositories.storyboards import StoryboardRepository
from backend_core.services.cost import estimate_shot
from backend_core.services.jobs import JobService
from backend_core.services.projects import ProjectService

logger = get_logger(__name__)


class ShotGenerationService:
    """Turns storyboard shots into queued generation jobs."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._repo = StoryboardRepository(session)
        self._jobs = JobService(session, settings=self._settings)
        self._projects = ProjectService(session)

    async def queue_shot(
        self, *, workspace_id: uuid.UUID, storyboard_id: uuid.UUID, shot_id: uuid.UUID
    ) -> tuple[GenerationJob, bool]:
        """Queue one shot. Returns `(job, created)` per §23."""
        shot = await self._repo.get_shot(workspace_id, storyboard_id, shot_id)
        if shot is None:
            raise ValidationError("Shot not found.", details={"shot_id": str(shot_id)})
        if not shot.visual_prompt.strip():
            # Nothing to send. §19's compiler should have produced one, so this
            # is a bug elsewhere surfacing here rather than a user error.
            raise ValidationError(
                "This shot has no compiled prompt.", details={"shot_id": str(shot_id)}
            )
        return await self._queue(workspace_id, shot)

    async def queue_storyboard(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, storyboard_id: uuid.UUID
    ) -> list[GenerationJob]:
        """Queue every shot of an approved storyboard.

        Approved only. Generating from a draft would spend money on shots a
        person may still be editing, and the approval step exists precisely to
        mark the moment that stops being true.
        """
        storyboard = await self._repo.get(workspace_id, project_id, storyboard_id)
        if storyboard is None:
            raise ValidationError(
                "Storyboard not found.", details={"storyboard_id": str(storyboard_id)}
            )
        if storyboard.status is not StoryboardStatus.APPROVED:
            raise ValidationError(
                "Approve the storyboard before generating its shots.",
                details={"status": storyboard.status.value},
            )

        shots = await self._repo.list_shots(workspace_id, storyboard_id)
        jobs: list[GenerationJob] = []
        for shot in shots:
            if shot.status is ShotStatus.READY:
                # Already has a chosen take. Re-generating is a per-shot action
                # (§103 rule 10), not something a bulk queue should decide.
                continue
            job, _ = await self._queue(workspace_id, shot)
            jobs.append(job)

        logger.info(
            "storyboard_queued",
            extra={
                "storyboard_id": str(storyboard_id),
                "queued": len(jobs),
                "shots": len(shots),
            },
        )
        return jobs

    async def _queue(self, workspace_id: uuid.UUID, shot: Shot) -> tuple[GenerationJob, bool]:
        provider = get_video_provider(self._settings)
        project = await self._projects.get(workspace_id=workspace_id, project_id=shot.project_id)

        job, created = await self._jobs.create(
            workspace_id=workspace_id,
            job_type=JobType.VIDEO_GENERATION,
            provider=provider.name,
            idempotency_key=_shot_key(shot),
            project_id=shot.project_id,
            shot_id=shot.id,
            input_json={
                # The prompt is captured here, not looked up at run time: what
                # was sent must be what is inspectable afterwards, and a shot
                # edited after queueing must not silently change the take.
                "prompt": shot.visual_prompt,
                "negative_prompt": shot.negative_prompt,
                "duration_seconds": shot.duration_seconds,
                "aspect_ratio": project.aspect_ratio.value,
                "shot_type": shot.shot_type.value,
                "identity_lock": shot.identity_lock,
            },
            estimated_cost=_estimated_cost(shot.duration_seconds, project.quality_mode),
        )

        if created and shot.status is ShotStatus.PENDING:
            shot.status = ShotStatus.QUEUED
            await self._session.flush()

        return job, created


def _shot_key(shot: Shot) -> str:
    """A deterministic idempotency key for one shot's current prompt (§23).

    Includes the prompt hash so that editing a shot and regenerating produces a
    genuinely new job — the take will differ, and returning the old one would
    be wrong. Two identical requests for an unedited shot collapse into one.
    """
    digest = hashlib.sha256()
    digest.update(str(shot.id).encode())
    digest.update(shot.visual_prompt.encode())
    digest.update(str(shot.duration_seconds).encode())
    return f"shot:{shot.id}:{digest.hexdigest()[:16]}"


def _estimated_cost(duration_seconds: float, quality_mode: QualityMode) -> float:
    """What to reserve for one shot (§22, P18-T06).

    The *maximum*, not the expected value. A reservation exists to stop a job
    starting that cannot be paid for, and reserving the expected cost would let
    a shot that overran its estimate be discovered at capture — by which point
    the provider has already been paid.
    """
    return estimate_shot(duration_seconds, quality_mode).maximum


__all__ = ["ShotGenerationService"]
