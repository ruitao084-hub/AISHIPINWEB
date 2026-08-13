"""Render worker (§34, §35, PHASE 13).

§34's pipeline, in order: load the timeline, download assets, probe, build the
plan, run ffmpeg without shell interpolation, validate, thumbnail, upload,
create a MediaAsset, complete.

Everything happens inside one temporary directory that is removed on the way
out — §35 requires isolated temp dirs and cleanup, and a worker that leaks a
500 MB working directory per render fills its disk in a day.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import (
    AssetSourceType,
    AssetType,
    JobStatus,
    ProjectStatus,
    RenderStatus,
    UploadStatus,
)
from backend_core.domain.models import MediaAsset, Render
from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger
from backend_core.render.plan import build_render_plan, build_thumbnail_plan
from backend_core.render.qc import check_rendered_video
from backend_core.render.timeline import Timeline, load_timeline
from backend_core.services.jobs import JobService
from backend_core.services.projects import ProjectService
from backend_core.storage.keys import project_thumbnail_key, render_output_key
from backend_core.storage.s3 import get_storage

logger = get_logger(__name__)


class RenderFailedError(AppError):
    """ffmpeg could not produce a usable file."""

    code = ErrorCode.RENDER_FAILED
    http_status = 500
    default_message = "The video could not be rendered."


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    job_id: uuid.UUID
    status: JobStatus
    render_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None


class RenderJobRunner:
    """Composes a project's clips into one video (§34)."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def run(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> RenderOutcome:
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            render_id = uuid.UUID(str(job.input_json["render_id"]))
            project_id = job.project_id
            await jobs.transition(job, JobStatus.QUEUED)
            await jobs.transition(job, JobStatus.PROCESSING)
            await session.commit()

        if project_id is None:
            raise ValueError("A render job must belong to a project.")

        async with get_async_sessionmaker()() as session:
            render = await session.get(Render, render_id)
            if render is None or render.workspace_id != workspace_id:
                raise ValueError("Render not found.")
            timeline, _ = load_timeline(render.timeline_json)
            render.status = RenderStatus.RENDERING
            await session.commit()

        try:
            output_path, thumbnail_path, probe_ms = await asyncio.to_thread(self._render, timeline)
        except RenderFailedError as exc:
            async with get_async_sessionmaker()() as session:
                render = await session.get(Render, render_id)
                if render is not None:
                    render.status = RenderStatus.FAILED
                    render.error_message = str(exc)[:2000]
                jobs = JobService(session, settings=self._settings)
                job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
                await jobs.fail(job, error_code="RENDER_FAILED", error_message=str(exc))
                await session.commit()
            return RenderOutcome(job_id=job_id, status=JobStatus.FAILED, render_id=render_id)

        try:
            async with get_async_sessionmaker()() as session:
                storage = get_storage()

                output_key = render_output_key(workspace_id, project_id, render_id)
                storage.upload_file(output_path, output_key, "video/mp4")
                video_asset = MediaAsset(
                    workspace_id=workspace_id,
                    asset_type=AssetType.VIDEO,
                    source_type=AssetSourceType.RENDERED,
                    bucket=self._settings.s3_bucket,
                    object_key=output_key,
                    original_filename="final.mp4",
                    mime_type="video/mp4",
                    size_bytes=output_path.stat().st_size,
                    width=timeline.canvas.width,
                    height=timeline.canvas.height,
                    duration_ms=probe_ms,
                    fps=float(timeline.canvas.fps),
                    upload_status=UploadStatus.READY,
                )
                session.add(video_asset)

                thumbnail_asset_id: uuid.UUID | None = None
                if thumbnail_path.is_file():
                    thumb_key = project_thumbnail_key(workspace_id, project_id)
                    storage.upload_file(thumbnail_path, thumb_key, "image/jpeg")
                    thumbnail = MediaAsset(
                        workspace_id=workspace_id,
                        asset_type=AssetType.THUMBNAIL,
                        source_type=AssetSourceType.DERIVED,
                        bucket=self._settings.s3_bucket,
                        object_key=thumb_key,
                        original_filename="poster.jpg",
                        mime_type="image/jpeg",
                        size_bytes=thumbnail_path.stat().st_size,
                        upload_status=UploadStatus.READY,
                    )
                    session.add(thumbnail)
                    await session.flush()
                    thumbnail_asset_id = thumbnail.id

                await session.flush()

                render = await session.get(Render, render_id)
                if render is not None:
                    render.status = RenderStatus.COMPLETED
                    render.output_asset_id = video_asset.id
                    render.thumbnail_asset_id = thumbnail_asset_id
                    render.duration_ms = probe_ms
                    render.width = timeline.canvas.width
                    render.height = timeline.canvas.height

                jobs = JobService(session, settings=self._settings)
                job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
                await jobs.complete(job, result_asset_id=video_asset.id)

                projects = ProjectService(session)
                project = await projects.get(workspace_id=workspace_id, project_id=project_id)
                if project.status is ProjectStatus.COMPOSITING:
                    await projects.transition(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        target=ProjectStatus.QC
                        if self._settings.enable_qc
                        else ProjectStatus.READY,
                    )

                asset_id = video_asset.id
                await session.commit()
        finally:
            # §35's cleanup. The workspace holds every downloaded clip, so
            # leaking one per render fills a worker's disk within a day.
            _cleanup(output_path.parent)

        logger.info(
            "render_completed",
            extra={"render_id": str(render_id), "duration_ms": probe_ms},
        )
        return RenderOutcome(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            render_id=render_id,
            asset_id=asset_id,
        )

    def _render(self, timeline: Timeline) -> tuple[Path, Path, int]:
        """Download, encode and validate. Runs off the event loop."""
        workspace = Path(tempfile.mkdtemp(prefix="aipvs-render-"))
        storage = get_storage()

        local_paths: dict[str, Path] = {}

        def fetch(key: str) -> None:
            if not key or key in local_paths:
                return
            destination = workspace / f"{len(local_paths):03d}_{Path(key).name}"
            storage.download_file(key, destination)
            local_paths[key] = destination

        for track in timeline.tracks:
            for item in track.items:
                if item.object_key:
                    fetch(item.object_key)

                # PHASE 20's logo overlay rides in an item's metadata rather
                # than on a track of its own (§33 fixes the four). §141 makes a
                # missing logo a skip, not a failure, so a download that fails
                # here must not take the render with it.
                logo = item.metadata.get("logo")
                if isinstance(logo, dict) and isinstance(logo.get("object_key"), str):
                    try:
                        fetch(str(logo["object_key"]))
                    except Exception:
                        logger.warning(
                            "logo_download_failed",
                            extra={"object_key": str(logo["object_key"])},
                        )

        plan = build_render_plan(
            timeline,
            workspace=workspace,
            local_paths=local_paths,
            ffmpeg_path=self._settings.ffmpeg_path,
        )
        for path, body in plan.sidecar_files.items():
            path.write_text(body, encoding="utf-8")

        try:
            completed = subprocess.run(  # noqa: S603 — fixed argv list, shell=False (§35)
                plan.argv,
                capture_output=True,
                text=True,
                timeout=self._settings.ffmpeg_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderFailedError("The render exceeded its time limit.") from exc

        if completed.returncode != 0:
            # ffmpeg's last stderr line is the actionable part; the rest is
            # banner noise even at `-loglevel error`.
            detail = (completed.stderr or "").strip().splitlines()
            raise RenderFailedError(f"ffmpeg failed: {detail[-1] if detail else 'no output'}")

        report = check_rendered_video(
            plan.output_path,
            expected_width=timeline.canvas.width,
            expected_height=timeline.canvas.height,
            expected_duration_ms=plan.expected_duration_ms,
            expect_audio=any(item.object_key for track in timeline.tracks for item in track.items),
            settings=self._settings,
        )
        if report.blocking_findings:
            raise RenderFailedError(
                "The rendered file failed validation: "
                + "; ".join(finding.detail for finding in report.blocking_findings)
            )

        thumbnail_path = workspace / "poster.jpg"
        subprocess.run(  # noqa: S603 — fixed argv list, shell=False (§35)
            build_thumbnail_plan(
                plan.output_path,
                output_path=thumbnail_path,
                ffmpeg_path=self._settings.ffmpeg_path,
            ),
            capture_output=True,
            timeout=120,
            check=False,
        )

        duration_ms = (
            report.probed.duration_ms
            if report.probed and report.probed.duration_ms
            else plan.expected_duration_ms
        )
        return plan.output_path, thumbnail_path, duration_ms


def _cleanup(directory: Path) -> None:
    import shutil

    shutil.rmtree(directory, ignore_errors=True)


__all__ = ["RenderFailedError", "RenderJobRunner", "RenderOutcome"]
