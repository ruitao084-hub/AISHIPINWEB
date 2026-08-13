"""The worker half of §22's orchestrator (P9-T04 through T09, P11).

§22 lists the steps and this module is them, in order:

    Pop → Lock → Submit → Save ProviderJob → Poll → Download → Validate →
    Store → Create MediaAsset → Complete → Capture credits

Two details carry most of the correctness.

**Locking.** Two workers popping the same job would submit it twice and bill
twice. The lock is a Redis `SET NX` on the job id, held for the length of the
attempt — PHASE 1 built exactly this primitive for exactly this.

**The poll loop does not hold a transaction open.** A generation takes minutes;
a database transaction held for minutes exhausts the pool and blocks every
other write to that row. So each step opens a session, does its work, commits,
and closes — and the loop between them holds nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend_core.cache import get_redis
from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import AssetType, JobStatus, ShotStatus
from backend_core.domain.models import Shot, ShotReference
from backend_core.errors import (
    AppError,
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.jobs.ingestion import IngestionError, ingest_provider_media
from backend_core.observability import get_logger
from backend_core.providers.video import (
    ProviderJobState,
    VideoProvider,
    VideoRequest,
    VideoStatus,
    get_video_provider,
)
from backend_core.services.jobs import JobService
from backend_core.storage.s3 import get_storage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What happened to one attempt."""

    job_id: uuid.UUID
    status: JobStatus
    asset_id: uuid.UUID | None = None
    error_code: str | None = None
    #: Whether the orchestrator scheduled another attempt (§24).
    will_retry: bool = False
    #: Which attempt this was, so the caller can size the backoff.
    retry_count: int = 0


class JobLockedError(RuntimeError):
    """Another worker already holds this job."""


class VideoJobRunner:
    """Runs one video-generation job end to end (§22).

    Constructed per job rather than per worker: it holds the provider and the
    lock for one attempt, and sharing either across jobs is how two jobs end up
    polling each other's handles.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider: VideoProvider | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider or get_video_provider(self._settings)

    async def run(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> JobOutcome:
        """Execute one attempt.

        The lock is held for the whole attempt and released in `finally`, so a
        worker that dies mid-generation frees the job when the lock's TTL
        expires rather than wedging it forever.
        """
        redis = get_redis()
        lock_key = f"joblock:{job_id}"
        acquired = await redis.set(
            lock_key, "1", nx=True, ex=self._settings.video_job_timeout_seconds
        )
        if not acquired:
            raise JobLockedError(f"Job {job_id} is already being processed.")

        try:
            return await self._run_locked(workspace_id, job_id)
        finally:
            await redis.delete(lock_key)

    async def _run_locked(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> JobOutcome:
        request, shot_id, project_id = await self._load_request(workspace_id, job_id)

        # §55's router picks the provider for *this attempt* (PHASE 19). Doing
        # it per attempt rather than per job is what makes fallback work: a job
        # that failed against one provider is re-queued by §24 and routed again,
        # and by then the breaker has opened, so it lands somewhere else.
        provider = await self._select_provider(workspace_id, job_id)

        # -- submit ---------------------------------------------------------
        try:
            submission = await asyncio.to_thread(provider.submit, request)
        except (ProviderRejectedError, ProviderUnavailableError, ProviderRateLimitedError) as exc:
            await self._note_provider_failure(provider.name)
            return await self._record_failure(workspace_id, job_id, exc)

        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            await jobs.transition(job, JobStatus.SUBMITTED)
            await jobs.record_submission(
                job,
                provider=provider.name,
                provider_job_id=submission.provider_job_id,
                request_redacted=submission.request_redacted,
            )
            await session.commit()

        # -- poll -----------------------------------------------------------
        try:
            status = await self._poll_until_done(workspace_id, job_id, submission.provider_job_id)
        except (ProviderRejectedError, ProviderUnavailableError, ProviderRateLimitedError) as exc:
            return await self._record_failure(workspace_id, job_id, exc)
        except TimeoutError:
            return await self._record_timeout(workspace_id, job_id)

        if status.state is ProviderJobState.CANCELED:
            async with get_async_sessionmaker()() as session:
                jobs = JobService(session, settings=self._settings)
                await jobs.cancel(workspace_id=workspace_id, job_id=job_id)
                await session.commit()
            return JobOutcome(job_id=job_id, status=JobStatus.CANCELED)

        if status.state is ProviderJobState.FAILED or not status.result_url:
            return await self._record_failure(
                workspace_id,
                job_id,
                ProviderRejectedError(
                    status.error_message or "The provider did not produce a result.",
                    details={"provider_error": status.error_code},
                ),
            )

        # -- ingest (§27) ---------------------------------------------------
        try:
            async with get_async_sessionmaker()() as session:
                asset = await ingest_provider_media(
                    session,
                    workspace_id=workspace_id,
                    source_url=status.result_url,
                    asset_type=AssetType.VIDEO,
                    expected_mime_type="video/mp4",
                    project_id=project_id,
                    shot_id=shot_id,
                    settings=self._settings,
                    filename="shot.mp4",
                )
                asset_id = asset.id
                await session.commit()
        except IngestionError as exc:
            return await self._record_failure(workspace_id, job_id, exc)

        # -- complete -------------------------------------------------------
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            await jobs.complete(
                job,
                result_asset_id=asset_id,
                output_json={"provider_job_id": submission.provider_job_id},
            )
            if shot_id is not None:
                await _mark_shot(session, workspace_id, shot_id, job.id, ShotStatus.READY)
            await session.commit()

        # A success closes the breaker if this attempt was the one that proved
        # the provider had recovered (P19-T05).
        await self._note_provider_success(provider.name)

        logger.info("video_job_completed", extra={"job_id": str(job_id), "asset_id": str(asset_id)})
        return JobOutcome(job_id=job_id, status=JobStatus.COMPLETED, asset_id=asset_id)

    # -- steps --------------------------------------------------------------

    async def _load_request(
        self, workspace_id: uuid.UUID, job_id: uuid.UUID
    ) -> tuple[VideoRequest, uuid.UUID | None, uuid.UUID]:
        """Build the provider request from the job's stored input.

        The prompt comes off the job row rather than being recompiled here.
        What was sent has to be what is inspectable afterwards, and recompiling
        would silently pick up any edit made since the job was queued.
        """
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)

            if job.status not in (JobStatus.CREATED, JobStatus.QUEUED):
                raise JobLockedError(f"Job {job_id} is {job.status.value}, not waiting to be run.")

            payload = dict(job.input_json)
            project_id = job.project_id
            shot_id = job.shot_id

            references: list[bytes] = []
            if shot_id is not None:
                references = await _load_reference_images(session, workspace_id, shot_id)

            if job.status is JobStatus.CREATED:
                await jobs.transition(job, JobStatus.QUEUED)
            await session.commit()

        if project_id is None:
            raise ValueError("A video job must belong to a project.")

        return (
            VideoRequest(
                prompt=str(payload.get("prompt", "")),
                negative_prompt=str(payload.get("negative_prompt", "")),
                duration_seconds=float(payload.get("duration_seconds", 5.0)),
                aspect_ratio=str(payload.get("aspect_ratio", "9:16")),
                reference_images=references,
            ),
            shot_id,
            project_id,
        )

    async def _poll_until_done(
        self, workspace_id: uuid.UUID, job_id: uuid.UUID, provider_job_id: str
    ) -> VideoStatus:
        """Poll until the provider finishes, or the job's budget runs out.

        `TimeoutError` rather than an infinite loop: §161 wants a stuck job
        detected, and a worker that waits forever is the thing that made it
        stuck.
        """
        deadline = datetime.now(UTC).timestamp() + self._settings.video_job_timeout_seconds
        first = True

        while True:
            if not first:
                await asyncio.sleep(self._settings.video_poll_interval_seconds)
            first = False

            status = await asyncio.to_thread(self._provider.poll, provider_job_id)

            async with get_async_sessionmaker()() as session:
                jobs = JobService(session, settings=self._settings)
                job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
                if job.status is JobStatus.SUBMITTED and status.state is ProviderJobState.RUNNING:
                    await jobs.transition(job, JobStatus.PROCESSING, progress=status.progress)
                elif job.status is JobStatus.PROCESSING:
                    job.progress = max(0, min(100, status.progress))
                attempt = await jobs._repo.latest_attempt(job_id)
                if attempt is not None:
                    await jobs.record_result(
                        attempt,
                        provider_status=status.state.value,
                        finished=status.state
                        in (
                            ProviderJobState.SUCCEEDED,
                            ProviderJobState.FAILED,
                            ProviderJobState.CANCELED,
                        ),
                    )
                await session.commit()

            if status.state is not ProviderJobState.PENDING and (
                status.state is not ProviderJobState.RUNNING
            ):
                return status

            if datetime.now(UTC).timestamp() > deadline:
                raise TimeoutError(f"Job {job_id} exceeded its generation budget.")

    async def _select_provider(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> VideoProvider:
        """Route this attempt, or fall back to the configured single provider.

        With `ENABLE_MULTI_PROVIDER` off there is nothing to route between, and
        the flag exists precisely so the router can be built and left dark
        (§122). The routed provider is recorded on the job so "why did this
        come from Runway" is answerable from the row.
        """
        if not self._settings.enable_multi_provider:
            return self._provider

        from backend_core.providers.video import build_video_provider
        from backend_core.services.router import NoProviderAvailableError, route_for_job

        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            try:
                decision = await route_for_job(session, job, settings=self._settings)
            except NoProviderAvailableError:
                # Every provider is unavailable. Falling through to the default
                # lets the attempt fail against a real provider and be retried
                # by §24, which is better than a routing error the user cannot
                # act on.
                logger.warning("routing_fell_back_to_default", extra={"job_id": str(job_id)})
                return self._provider

            job.provider = decision.provider
            job.model = decision.model
            job.input_json = {**job.input_json, "routing_reason": decision.reason}
            await session.commit()

        return build_video_provider(decision.provider, self._settings)

    async def _note_provider_failure(self, provider_name: str) -> None:
        """Tell the breaker (P19-T05). Best effort — never fails the job."""
        if not self._settings.enable_multi_provider:
            return
        from backend_core.services.router import ProviderRouter

        try:
            async with get_async_sessionmaker()() as session:
                await ProviderRouter(session, settings=self._settings).record_failure(provider_name)
                await session.commit()
        except Exception:
            logger.warning("circuit_breaker_update_failed", extra={"provider": provider_name})

    async def _note_provider_success(self, provider_name: str) -> None:
        if not self._settings.enable_multi_provider:
            return
        from backend_core.services.router import ProviderRouter

        try:
            async with get_async_sessionmaker()() as session:
                await ProviderRouter(session, settings=self._settings).record_success(provider_name)
                await session.commit()
        except Exception:
            logger.warning("circuit_breaker_update_failed", extra={"provider": provider_name})

    async def _record_failure(
        self, workspace_id: uuid.UUID, job_id: uuid.UUID, exc: Exception
    ) -> JobOutcome:
        """Record a failure and let §24 decide about a retry."""
        code = exc.code.value if isinstance(exc, AppError) else "PROVIDER_UNAVAILABLE"

        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            job, will_retry = await jobs.fail(job, error_code=code, error_message=str(exc))
            retry_count = job.retry_count
            if job.shot_id is not None and not will_retry:
                await _mark_shot(session, workspace_id, job.shot_id, job.id, ShotStatus.FAILED)
            status = job.status
            await session.commit()

        return JobOutcome(
            job_id=job_id,
            status=status,
            error_code=code,
            will_retry=will_retry,
            retry_count=retry_count,
        )

    async def _record_timeout(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> JobOutcome:
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            await jobs.transition(
                job,
                JobStatus.TIMEOUT,
                error_code="JOB_TIMEOUT",
                error_message="The provider did not finish within the allowed time.",
            )
            await session.commit()
        return JobOutcome(job_id=job_id, status=JobStatus.TIMEOUT, error_code="JOB_TIMEOUT")


async def _load_reference_images(
    session: AsyncSession, workspace_id: uuid.UUID, shot_id: uuid.UUID
) -> list[bytes]:
    """Fetch the identity frames a locked shot must match (§29).

    Bytes, not URLs — handing a provider a presigned URL into our bucket leaks
    a credential and makes the call depend on our storage being reachable from
    their network.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)

    result = await session.execute(
        select(ShotReference)
        .options(selectinload(ShotReference.media_asset))
        .where(
            ShotReference.workspace_id == workspace_id,
            ShotReference.shot_id == shot_id,
        )
    )
    storage = get_storage()
    images: list[bytes] = []
    for reference in result.scalars().all():
        images.append(await asyncio.to_thread(storage.get_bytes, reference.media_asset.object_key))
    return images


async def _mark_shot(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    shot_id: uuid.UUID,
    job_id: uuid.UUID,
    status: ShotStatus,
) -> None:
    """Point a shot at the job that produced its clip."""
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)

    values: dict[str, object] = {"status": status}
    if status is ShotStatus.READY:
        values["selected_generation_job_id"] = job_id

    await session.execute(
        update(Shot).where(Shot.id == shot_id, Shot.workspace_id == workspace_id).values(**values)
    )


__all__ = ["JobLockedError", "JobOutcome", "VideoJobRunner"]
