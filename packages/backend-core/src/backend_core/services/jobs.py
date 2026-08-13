"""Job orchestration (§22, §23, §24, §106, P9-T05 through T09).

§22 fixes the shape of both halves.

**The API side** validates, creates a job, reserves credits, enqueues, and
returns `202` with a job id. It does *not* wait — §0.1 rule 13 forbids an HTTP
request blocking on video generation, and the whole of this module exists so
that it never has to.

**The worker side** pops, locks, submits, records the provider attempt, polls,
downloads, validates, stores, creates a `MediaAsset`, completes the job, and
captures credits. Failure branches on §24's taxonomy: retryable goes back to
the queue with exponential backoff and jitter, permanent goes to `FAILED`.
Either way the reservation is released.

Two things here are easy to get subtly wrong and are therefore explicit.

**Idempotency (§23)** is enforced by a unique constraint, not a check. A
read-then-insert loses the race between two simultaneous retries of the same
request; the constraint does not, and the `IntegrityError` path returns the
original job rather than surfacing a 500.

**Backoff has jitter.** Without it, twenty jobs failing against the same rate
limit retry in lockstep and rate-limit each other again, forever.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import (
    QUEUE_FOR_JOB_TYPE,
    FailureKind,
    JobStatus,
    JobType,
    QueueName,
    can_transition_job,
    classify_failure,
    is_terminal_job,
)
from backend_core.domain.models import GenerationJob, ProviderJob
from backend_core.errors import AppError, ErrorCode, NotFoundError
from backend_core.observability import get_logger
from backend_core.repositories.jobs import JobRepository
from backend_core.services.credits import CreditService, get_credit_service

logger = get_logger(__name__)


class InvalidJobTransitionError(AppError):
    """Refused by §106's job machine."""

    code = ErrorCode.PROJECT_INVALID_STATE
    http_status = 409
    default_message = "That job status change is not allowed."


class JobService:
    """Creates jobs, moves them through §106, and decides about retries."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        credits: CreditService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._repo = JobRepository(session)
        self._credits = credits or get_credit_service(self._settings)

    # -- creation (§22, §23) ------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        job_type: JobType,
        provider: str,
        idempotency_key: str,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        model: str | None = None,
        input_json: dict[str, Any] | None = None,
        estimated_cost: float = 0.0,
    ) -> tuple[GenerationJob, bool]:
        """Create a job, or return the one this key already made (§23).

        Returns `(job, created)`. The flag matters to the caller: a repeat
        should answer `200` with the original job rather than `202` with a new
        one, and a client that cannot tell the difference will double-count.

        Credits are reserved *before* enqueueing (§22). A job that cannot be
        paid for should never reach a worker, because by then the money is
        spent whether or not the ledger agrees.
        """
        existing = await self._repo.get_by_idempotency_key(workspace_id, idempotency_key)
        if existing is not None:
            logger.info(
                "job_idempotent_hit",
                extra={"job_id": str(existing.id), "idempotency_key": idempotency_key},
            )
            return existing, False

        job = GenerationJob(
            workspace_id=workspace_id,
            project_id=project_id,
            shot_id=shot_id,
            job_type=job_type,
            provider=provider,
            model=model,
            status=JobStatus.CREATED,
            idempotency_key=idempotency_key,
            input_json=input_json or {},
            estimated_cost=estimated_cost,
            max_retries=self._settings.job_max_retries,
        )
        self._session.add(job)

        try:
            await self._session.flush()
        except IntegrityError:
            # The race the read above cannot close: two identical requests
            # arriving together both found nothing. The constraint refused the
            # second, which is the correct outcome — roll back to the savepoint
            # and return the winner.
            await self._session.rollback()
            winner = await self._repo.get_by_idempotency_key(workspace_id, idempotency_key)
            if winner is None:
                raise
            return winner, False

        # §22's ordering. Raises InsufficientCreditsError, which §24 classifies
        # as permanent — retrying a job a workspace cannot afford fails the
        # same way every time.
        self._credits.reserve(workspace_id=workspace_id, job_id=job.id, amount=estimated_cost)

        logger.info(
            "job_created",
            extra={
                "job_id": str(job.id),
                "job_type": job_type.value,
                "provider": provider,
                "queue": self.queue_for(job).value,
            },
        )
        return job, True

    def queue_for(self, job: GenerationJob) -> QueueName:
        """Which of §25's queues this job belongs on."""
        return QUEUE_FOR_JOB_TYPE.get(job.job_type, QueueName.DEFAULT)

    # -- reads --------------------------------------------------------------

    async def get(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
        job = await self._repo.get(workspace_id, job_id)
        if job is None:
            raise NotFoundError("Job not found.", details={"job_id": str(job_id)})
        return job

    async def list_for_project(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        job_type: JobType | None = None,
    ) -> list[GenerationJob]:
        return await self._repo.list_for_project(workspace_id, project_id, job_type=job_type)

    # -- transitions (§106) -------------------------------------------------

    async def transition(
        self,
        job: GenerationJob,
        target: JobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        progress: int | None = None,
    ) -> GenerationJob:
        """Move a job, or refuse (§106).

        Stamps `started_at` on the first move out of `CREATED`/`QUEUED` and
        `finished_at` on arrival at a terminal state, so "how long did this
        take" is answerable without reconstructing it from logs.
        """
        if not can_transition_job(job.status, target):
            raise InvalidJobTransitionError(
                f"A job cannot go from {job.status.value} to {target.value}.",
                details={"current": job.status.value, "requested": target.value},
            )

        if target is JobStatus.FAILED and not error_code:
            # The CHECK constraint would refuse this anyway; failing here names
            # the caller instead of the row.
            raise InvalidJobTransitionError("A failed job must record an error code.")

        previous = job.status
        job.status = target

        if target in (JobStatus.SUBMITTED, JobStatus.PROCESSING) and job.started_at is None:
            job.started_at = datetime.now(UTC)
        if is_terminal_job(target):
            job.finished_at = datetime.now(UTC)
        if target is JobStatus.COMPLETED:
            job.progress = 100
        elif progress is not None:
            job.progress = max(0, min(100, progress))

        if error_code:
            job.error_code = error_code
            job.error_message = (error_message or "")[:2000] or None
        elif target is JobStatus.QUEUED:
            # A retry clears the previous failure: leaving it would make the
            # next inspection read as though the new attempt had already failed.
            job.error_code = None
            job.error_message = None

        await self._session.flush()
        logger.info(
            "job_transitioned",
            extra={
                "job_id": str(job.id),
                "from": previous.value,
                "to": target.value,
                "error_code": error_code,
            },
        )
        return job

    # -- completion and failure (§22, §24) ----------------------------------

    async def complete(
        self,
        job: GenerationJob,
        *,
        result_asset_id: uuid.UUID | None = None,
        output_json: dict[str, Any] | None = None,
        actual_cost: float | None = None,
    ) -> GenerationJob:
        """Finish a job successfully and capture its credits (§22)."""
        job.result_asset_id = result_asset_id
        job.output_json = output_json or {}
        job.actual_cost = actual_cost if actual_cost is not None else job.estimated_cost

        await self.transition(job, JobStatus.COMPLETED)
        self._credits.capture(
            workspace_id=job.workspace_id, job_id=job.id, amount=job.actual_cost or 0.0
        )
        return job

    async def fail(
        self,
        job: GenerationJob,
        *,
        error_code: str,
        error_message: str = "",
    ) -> tuple[GenerationJob, bool]:
        """Record a failure and decide whether to retry (§24).

        Returns `(job, will_retry)`. A retryable failure with attempts left
        goes back to `QUEUED` — the caller re-enqueues with the delay from
        :meth:`backoff_seconds`. Anything else lands in `FAILED` and the
        reservation is released.
        """
        kind = classify_failure(error_code)
        retryable = kind is FailureKind.RETRYABLE and job.retry_count < job.max_retries

        if retryable:
            job.retry_count += 1
            job.error_code = error_code
            job.error_message = error_message[:2000] or None
            await self._session.flush()
            # Re-queued from whatever state it was in. §106 permits this from
            # FAILED and TIMEOUT; from SUBMITTED or PROCESSING the job must
            # pass through FAILED first, which `_requeue` handles.
            await self._requeue(job)
            logger.info(
                "job_retry_scheduled",
                extra={
                    "job_id": str(job.id),
                    "attempt": job.retry_count,
                    "max_retries": job.max_retries,
                    "delay_seconds": self.backoff_seconds(job.retry_count),
                    "error_code": error_code,
                },
            )
            return job, True

        await self.transition(
            job, JobStatus.FAILED, error_code=error_code, error_message=error_message
        )
        self._credits.release(workspace_id=job.workspace_id, job_id=job.id)
        logger.warning(
            "job_failed",
            extra={
                "job_id": str(job.id),
                "error_code": error_code,
                "kind": kind.value,
                "retries_used": job.retry_count,
            },
        )
        return job, False

    async def _requeue(self, job: GenerationJob) -> None:
        """Return a job to the queue, via FAILED if §106 requires it."""
        if not can_transition_job(job.status, JobStatus.QUEUED):
            await self.transition(
                job,
                JobStatus.FAILED,
                error_code=job.error_code or "PROVIDER_UNAVAILABLE",
                error_message=job.error_message or "",
            )
        await self.transition(job, JobStatus.QUEUED)

    async def cancel(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob:
        """Stop a job at the user's request.

        A job already finished is left alone rather than erroring: the user
        wanted it stopped, and it is stopped. Raising here would be pedantry
        about a race they cannot see.
        """
        job = await self.get(workspace_id=workspace_id, job_id=job_id)
        if is_terminal_job(job.status):
            return job

        await self.transition(job, JobStatus.CANCELED)
        self._credits.release(workspace_id=workspace_id, job_id=job.id)
        return job

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff with jitter (§24).

        Jitter is not optional. Twenty jobs failing against one rate limit
        would otherwise retry in lockstep and rate-limit each other again, and
        again, at widening intervals — a thundering herd that looks like a
        provider outage.
        """
        base = self._settings.job_retry_base_delay_seconds * (2 ** max(0, attempt - 1))
        capped = min(base, 900)
        return float(round(capped * (0.5 + random.random()), 2))  # noqa: S311 — jitter

    # -- provider attempts (§10.16) -----------------------------------------

    async def record_submission(
        self,
        job: GenerationJob,
        *,
        provider: str,
        provider_job_id: str | None,
        request_redacted: dict[str, Any],
    ) -> ProviderJob:
        """Open a `ProviderJob` row for this attempt.

        One row per attempt, never updated in place across retries — §106 wants
        a new attempt rather than a mutated one, and "what did the provider say
        the second time" is only answerable if both survive.
        """
        attempt = ProviderJob(
            generation_job_id=job.id,
            provider=provider,
            provider_job_id=provider_job_id,
            request_payload_redacted=request_redacted,
            submitted_at=datetime.now(UTC),
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def latest_attempt(self, job_id: uuid.UUID) -> ProviderJob | None:
        """The most recent attempt, for a poll loop to update."""
        return await self._repo.latest_attempt(job_id)

    async def record_result(
        self,
        attempt: ProviderJob,
        *,
        provider_status: str,
        response_redacted: dict[str, Any] | None = None,
        finished: bool = False,
    ) -> ProviderJob:
        attempt.provider_status = provider_status
        attempt.last_polled_at = datetime.now(UTC)
        if response_redacted is not None:
            attempt.response_payload_redacted = response_redacted
        if finished:
            attempt.completed_at = datetime.now(UTC)
        await self._session.flush()
        return attempt


__all__ = ["InvalidJobTransitionError", "JobService"]
