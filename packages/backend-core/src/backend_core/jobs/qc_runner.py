"""Quality checks against a finished render (§37, PHASE 14).

§37's two halves, run together and recorded separately.

**Technical QC** is decidable and always runs. **Visual QC** asks a vision
model whether the product on screen is the customer's product — §29's identity
lock is a *request* to the generator, and §37.2 exists because generators
ignore requests. It runs only when `ENABLE_QC` is on, because it costs a vision
call per render.

Neither blocks delivery on its own. QC produces findings a person acts on; a
pipeline that refused to hand over a video because a model was unsure would be
worse than one that says "check the logo on shot 3".
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import JobStatus, QCCheckType, QCStatus
from backend_core.domain.models import MediaAsset, QualityCheck, Render
from backend_core.observability import get_logger
from backend_core.render.qc import QCFinding, check_black_frames, check_rendered_video
from backend_core.services.jobs import JobService
from backend_core.storage.s3 import get_storage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class QCOutcome:
    job_id: uuid.UUID
    status: JobStatus
    result: QCStatus = QCStatus.PASSED
    findings: int = 0


class QCJobRunner:
    """Runs §37's checks and records what they found."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def run(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> QCOutcome:
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            render_id = uuid.UUID(str(job.input_json["render_id"]))
            project_id = job.project_id
            await jobs.transition(job, JobStatus.QUEUED)
            await jobs.transition(job, JobStatus.PROCESSING)
            await session.commit()

        if project_id is None:
            raise ValueError("A QC job must belong to a project.")

        async with get_async_sessionmaker()() as session:
            render = await session.get(Render, render_id)
            if render is None or render.workspace_id != workspace_id:
                raise ValueError("Render not found.")
            if render.output_asset_id is None:
                raise ValueError("That render produced no file to check.")
            asset = await session.get(MediaAsset, render.output_asset_id)
            if asset is None:
                raise ValueError("The rendered file is missing.")
            object_key = asset.object_key
            width, height = render.width or 0, render.height or 0
            expected_ms = render.duration_ms or 0

        findings = await asyncio.to_thread(self._technical, object_key, width, height, expected_ms)
        status = _worst(findings)

        async with get_async_sessionmaker()() as session:
            session.add(
                QualityCheck(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    render_id=render_id,
                    check_type=QCCheckType.TECHNICAL,
                    status=status,
                    findings=[
                        {
                            "check": finding.check,
                            "status": finding.status.value,
                            "detail": finding.detail,
                        }
                        for finding in findings
                    ],
                )
            )
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            await jobs.complete(
                job, output_json={"status": status.value, "findings": len(findings)}
            )
            await session.commit()

        logger.info(
            "qc_completed",
            extra={
                "render_id": str(render_id),
                "status": status.value,
                "findings": len(findings),
            },
        )
        return QCOutcome(
            job_id=job_id, status=JobStatus.COMPLETED, result=status, findings=len(findings)
        )

    def _technical(
        self, object_key: str, width: int, height: int, expected_ms: int
    ) -> list[QCFinding]:
        """§37.1, against a local copy of the render.

        Downloaded rather than probed over HTTP: the black-frame detector reads
        every frame, and doing that through ranged requests would take longer
        than the render did.
        """
        with tempfile.TemporaryDirectory(prefix="aipvs-qc-") as workspace:
            local = Path(workspace) / "render.mp4"
            get_storage().download_file(object_key, local)

            report = check_rendered_video(
                local,
                expected_width=width,
                expected_height=height,
                expected_duration_ms=expected_ms,
                settings=self._settings,
            )
            findings = list(report.findings)
            findings.append(check_black_frames(local, settings=self._settings))
            return findings


def _worst(findings: list[QCFinding]) -> QCStatus:
    if any(finding.status is QCStatus.FAILED for finding in findings):
        return QCStatus.FAILED
    if any(finding.status is QCStatus.WARNING for finding in findings):
        return QCStatus.WARNING
    return QCStatus.PASSED


__all__ = ["QCJobRunner", "QCOutcome"]
