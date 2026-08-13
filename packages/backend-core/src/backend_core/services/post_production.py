"""Voice, composition and quality checks — the second half of the pipeline.

PHASE 15's requirement is that the chain has no broken link:

    Generate → Voice → Render → QC → Download

PHASES 12-14 built the workers for those steps but nothing that *starts* them.
This module is that missing piece, and it is deliberately the only place that
knows how the three fit together.

The one decision worth stating: **the timeline is assembled here, not in the
render worker.** §33 forbids the worker guessing shot order, and the way to
make that true is for the worker to receive a finished `Timeline` and have no
query with which to guess. So this service reads the shots, follows each one to
its chosen take, lays the narration and subtitles alongside, and stores the
result on the `Render` row. Two renders of the same row then produce the same
video, and "what was actually composed" is answerable from the database rather
than reconstructed from worker logs.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import (
    JobType,
    ProjectStatus,
    RenderStatus,
    ShotStatus,
    TrackType,
)
from backend_core.domain.models import (
    GenerationJob,
    MediaAsset,
    QualityCheck,
    Render,
    Shot,
    Storyboard,
    VoiceoverTrack,
)
from backend_core.errors import NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.providers.tts import get_tts_provider
from backend_core.render.timeline import Timeline, build_timeline
from backend_core.repositories.renders import RenderRepository
from backend_core.repositories.storyboards import StoryboardRepository
from backend_core.services.jobs import JobService
from backend_core.services.projects import ProjectService

logger = get_logger(__name__)

#: How loud the music sits under narration. Low, because the MVP renderer does
#: not duck: it mixes at fixed gains, so the music has to be quiet enough that
#: the loudest line is still intelligible over it.
_BGM_GAIN: float = 0.18


class PostProductionService:
    """Starts voice, render and QC jobs, and assembles what they need."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._jobs = JobService(session, settings=self._settings)
        self._renders = RenderRepository(session)
        self._storyboards = StoryboardRepository(session)
        self._projects = ProjectService(session)

    # -- voice (§30, §31) ---------------------------------------------------

    async def queue_voiceover(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> tuple[GenerationJob, bool]:
        """Synthesise the approved storyboard's narration.

        Keyed on the storyboard rather than the project: re-approving a
        storyboard produces a new version and therefore new narration, while
        pressing the button twice on one version must not pay for two.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        storyboard = await self._require_approved_storyboard(workspace_id, project_id)

        provider = get_tts_provider(self._settings)
        return await self._jobs.create(
            workspace_id=workspace_id,
            job_type=JobType.TTS,
            provider=provider.name,
            idempotency_key=f"tts:{storyboard.id}:{project.language}",
            project_id=project_id,
            input_json={
                "storyboard_id": str(storyboard.id),
                "language": project.language,
            },
            estimated_cost=0.0,
        )

    # -- render (§33, §34) --------------------------------------------------

    async def create_render(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, burn_subtitles: bool = True
    ) -> tuple[Render, GenerationJob, bool]:
        """Build the timeline, store it, and queue the composition (§33, §34).

        Refuses a storyboard whose shots are not all generated. A render of
        four shots out of six is not a shorter video — it is a video missing
        its ending, and producing one silently would be worse than saying so.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        storyboard = await self._require_approved_storyboard(workspace_id, project_id)

        shots = await self._storyboards.list_shots(workspace_id, storyboard.id)
        renderable = [shot for shot in shots if shot.status is not ShotStatus.SKIPPED]
        if not renderable:
            raise ValidationError(
                "This storyboard has no shots to render.",
                details={"storyboard_id": str(storyboard.id)},
            )

        pending = [shot.sequence_no for shot in renderable if shot.status is not ShotStatus.READY]
        if pending:
            raise ValidationError(
                "Every shot must finish generating before the video can be composed.",
                details={"pending_shots": pending},
            )

        clips = await self._clips(workspace_id, renderable)
        voiceover = await self._renders.latest_voiceover(workspace_id, project_id)
        subtitles = await self._renders.latest_subtitles(workspace_id, project_id)

        voice_segments: list[tuple[str, str, int, int]] = []
        if voiceover is not None and voiceover.audio_asset_id is not None:
            audio = await self._renders.asset(workspace_id, voiceover.audio_asset_id)
            if audio is not None:
                # One item spanning the whole narration, not one per line: the
                # TTS runner concatenated the segments into a single file, so
                # placing each line separately would replay the whole track
                # once per line.
                voice_segments.append(
                    (
                        str(audio.id),
                        audio.object_key,
                        0,
                        max(voiceover.total_duration_ms, 1),
                    )
                )

        subtitle_cues: list[tuple[str, int, int]] = []
        if burn_subtitles and subtitles is not None:
            subtitle_cues = [
                (str(cue["text"]), int(cue["start_ms"]), int(cue["end_ms"]))
                for cue in subtitles.cues
                if str(cue.get("text", "")).strip()
            ]

        timeline = build_timeline(
            aspect_ratio=project.aspect_ratio.value,
            clips=clips,
            voice_segments=voice_segments or None,
            subtitle_cues=subtitle_cues or None,
            bgm_gain=_BGM_GAIN,
        )

        version = await self._renders.next_version(workspace_id, project_id)
        render = Render(
            workspace_id=workspace_id,
            project_id=project_id,
            storyboard_id=storyboard.id,
            version=version,
            status=RenderStatus.PENDING,
            timeline_json=timeline.model_dump(mode="json"),
            width=timeline.canvas.width,
            height=timeline.canvas.height,
            duration_ms=timeline.duration_ms,
        )
        self._session.add(render)
        await self._session.flush()

        job, created = await self._jobs.create(
            workspace_id=workspace_id,
            job_type=JobType.RENDER,
            provider="ffmpeg",
            # The timeline's fingerprint, not the render id: re-requesting a
            # composition of exactly the same material must not re-encode it,
            # while any edit — a new take, an added subtitle — must.
            idempotency_key=f"render:{project_id}:{_timeline_digest(timeline)}",
            project_id=project_id,
            input_json={"render_id": str(render.id), "storyboard_id": str(storyboard.id)},
            estimated_cost=0.0,
        )

        if project.status is ProjectStatus.GENERATING:
            await self._projects.transition(
                workspace_id=workspace_id,
                project_id=project_id,
                target=ProjectStatus.COMPOSITING,
            )

        logger.info(
            "render_created",
            extra={
                "render_id": str(render.id),
                "project_id": str(project_id),
                "version": version,
                "clips": len(clips),
                "duration_ms": timeline.duration_ms,
                "reused_job": not created,
            },
        )
        return render, job, created

    async def _clips(
        self, workspace_id: uuid.UUID, shots: list[Shot]
    ) -> list[tuple[str, str, int]]:
        """Follow each shot to the take that was chosen for it.

        Through `selected_generation_job_id` rather than "the newest job for
        this shot": a shot regenerated three times has three completed jobs,
        and the one a person picked is the only one that belongs in the video.
        """
        clips: list[tuple[str, str, int]] = []
        for shot in sorted(shots, key=lambda entry: entry.sequence_no):
            if shot.selected_generation_job_id is None:
                raise ValidationError(
                    "A shot is marked ready but has no chosen take.",
                    details={"shot_id": str(shot.id), "sequence_no": shot.sequence_no},
                )
            result = await self._session.execute(
                select(MediaAsset)
                .join(GenerationJob, GenerationJob.result_asset_id == MediaAsset.id)
                .where(
                    GenerationJob.id == shot.selected_generation_job_id,
                    GenerationJob.workspace_id == workspace_id,
                )
            )
            asset = result.scalar_one_or_none()
            if asset is None:
                raise ValidationError(
                    "The chosen take for a shot has no stored video.",
                    details={"shot_id": str(shot.id)},
                )
            # The shot's planned duration wins over the asset's actual length.
            # A provider that returns 5.2s for a 5s request would otherwise
            # drift the timeline against the narration it was timed to.
            duration_ms = round(shot.duration_seconds * 1000)
            clips.append((str(asset.id), asset.object_key, duration_ms))
        return clips

    # -- quality checks (§37) -----------------------------------------------

    async def queue_quality_check(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID
    ) -> tuple[GenerationJob, bool]:
        render = await self._require_render(workspace_id, project_id, render_id)
        if render.output_asset_id is None:
            raise ValidationError(
                "That render has not produced a file to check yet.",
                details={"render_id": str(render_id), "status": render.status.value},
            )

        job, created = await self._jobs.create(
            workspace_id=workspace_id,
            job_type=JobType.QC,
            provider="internal",
            idempotency_key=f"qc:{render_id}",
            project_id=project_id,
            input_json={"render_id": str(render_id)},
            estimated_cost=0.0,
        )

        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        if project.status is ProjectStatus.COMPOSITING:
            await self._projects.transition(
                workspace_id=workspace_id, project_id=project_id, target=ProjectStatus.QC
            )

        return job, created

    async def list_quality_checks(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID | None = None
    ) -> list[QualityCheck]:
        return await self._renders.list_quality_checks(workspace_id, project_id, render_id)

    # -- reads and delivery -------------------------------------------------

    async def list_renders(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> list[Render]:
        return await self._renders.list_for_project(workspace_id, project_id)

    async def get_render(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID
    ) -> Render:
        return await self._require_render(workspace_id, project_id, render_id)

    async def latest_voiceover(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> VoiceoverTrack | None:
        return await self._renders.latest_voiceover(workspace_id, project_id)

    async def download_target(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID | None = None
    ) -> MediaAsset:
        """The asset a download should hand over.

        Returns the asset rather than a URL: signing belongs to the API layer,
        which knows the expiry policy, and returning a URL from here would make
        this method untestable without a storage backend.
        """
        if render_id is None:
            render = await self._renders.latest_completed(workspace_id, project_id)
            if render is None:
                raise NotFoundError(
                    "This project has no finished video yet.",
                    details={"project_id": str(project_id)},
                )
        else:
            render = await self._require_render(workspace_id, project_id, render_id)

        if render.output_asset_id is None:
            raise ValidationError(
                "That render produced no file.",
                details={"render_id": str(render.id), "status": render.status.value},
            )
        asset = await self._renders.asset(workspace_id, render.output_asset_id)
        if asset is None:
            raise NotFoundError("The rendered file is missing.")
        return asset

    # -- helpers ------------------------------------------------------------

    async def _require_approved_storyboard(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Storyboard:
        storyboard = await self._storyboards.approved(workspace_id, project_id)
        if storyboard is None:
            raise ValidationError(
                "Approve a storyboard before composing the video.",
                details={"project_id": str(project_id)},
            )
        return storyboard

    async def _require_render(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID
    ) -> Render:
        render = await self._renders.get(workspace_id, project_id, render_id)
        if render is None:
            raise NotFoundError("Render not found.", details={"render_id": str(render_id)})
        return render


def _timeline_digest(timeline: Timeline) -> str:
    """A stable fingerprint of what would be composed.

    Over the tracks' content, so that re-requesting an identical composition is
    idempotent (§23) while any genuine edit produces a new job. The canvas is
    included because the same clips at a different aspect ratio are a different
    video.
    """
    digest = hashlib.sha256()
    digest.update(
        f"{timeline.canvas.width}x{timeline.canvas.height}@{timeline.canvas.fps}".encode()
    )
    for track in sorted(timeline.tracks, key=lambda entry: entry.type.value):
        digest.update(track.type.value.encode())
        for item in sorted(track.items, key=lambda entry: entry.start_ms):
            digest.update(
                f"{item.start_ms}:{item.end_ms}:{item.object_key or ''}:{item.text}".encode()
            )
    return digest.hexdigest()[:16]


def has_video_track(timeline: Timeline) -> bool:
    """Whether a stored timeline still describes something renderable."""
    track = timeline.track(TrackType.VIDEO)
    return track is not None and bool(track.items)


__all__ = ["PostProductionService", "has_video_track"]
