"""Recover jobs nobody is working on any more (§161, P16-T15).

A job can be left in `SUBMITTED` or `PROCESSING` with no worker behind it: the
worker was killed, the box was replaced, a provider stopped answering and the
poll loop died with the process. §106 has no transition out of that on its own,
so without this the job stays "in progress" forever, its credits stay reserved,
and the user watches a spinner that will never stop.

Three things must be true when a job is reaped, and getting any of them wrong
is worse than not reaping at all:

**Only genuinely stale jobs.** Staleness is measured from `started_at`, against
a threshold well above the longest legitimate run. Reaping a slow-but-alive job
would cancel work the user is paying for.

**Credits come back.** `JobService.fail` releases the reservation, which is why
this goes through it rather than writing `status = FAILED` directly.

**The lock is cleared.** `VideoJobRunner` takes a Redis lock with a TTL; a
crashed worker leaves it held until it expires. Deleting it lets a retry start
now instead of waiting out the remainder.

Whether a reaped job retries is §24's decision, not this module's. `JOB_TIMEOUT`
is classified retryable, so a job with attempts left goes back to the queue and
one that has exhausted them lands in `FAILED` — which is the behaviour anyone
would want and none of it is re-implemented here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.observability import get_logger
from backend_core.repositories.jobs import JobRepository
from backend_core.services.jobs import JobService

logger = get_logger(__name__)

#: Matches the key `VideoJobRunner` takes. Duplicated as one line rather than
#: imported, because importing the runner pulls in the whole provider stack for
#: a periodic task that only needs to delete a string.
_LOCK_KEY = "joblock:{job_id}"


@dataclass(frozen=True, slots=True)
class ReapReport:
    """What one sweep did."""

    examined: int
    reaped: tuple[uuid.UUID, ...] = ()
    requeued: tuple[uuid.UUID, ...] = ()

    @property
    def count(self) -> int:
        return len(self.reaped)


class StuckJobReaper:
    """Finds abandoned jobs and returns them to §106's machine."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def sweep(self, *, limit: int = 100) -> ReapReport:
        """Reap every job stuck longer than the configured threshold.

        Bounded by `limit` so one sweep cannot hold a transaction open over
        thousands of rows after a long outage. The next sweep takes the rest —
        recovery being gradual is better than a single sweep timing out and
        recovering nothing.
        """
        threshold = self._settings.job_stuck_threshold_seconds
        reaped: list[uuid.UUID] = []
        requeued: list[uuid.UUID] = []

        async with get_async_sessionmaker()() as session:
            stuck = await JobRepository(session).find_stuck(
                older_than_seconds=threshold, limit=limit
            )
            examined = len(stuck)

            for job in stuck:
                jobs = JobService(session, settings=self._settings)
                # Through `fail`, not a direct status write: it releases the
                # reservation and applies §24's retry policy, both of which a
                # hand-written UPDATE would silently skip.
                _, will_retry = await jobs.fail(
                    job,
                    error_code="JOB_TIMEOUT",
                    error_message=(
                        f"No worker reported on this job for over {threshold}s; "
                        "recovered by the stuck-job sweep."
                    ),
                )
                reaped.append(job.id)
                if will_retry:
                    requeued.append(job.id)

            await session.commit()

        for job_id in reaped:
            await self._release_lock(job_id)

        if reaped:
            logger.warning(
                "stuck_jobs_reaped",
                extra={
                    "examined": examined,
                    "reaped": len(reaped),
                    "requeued": len(requeued),
                    "threshold_seconds": threshold,
                },
            )
        return ReapReport(examined=examined, reaped=tuple(reaped), requeued=tuple(requeued))

    async def _release_lock(self, job_id: uuid.UUID) -> None:
        """Drop the Redis lock a dead worker is still nominally holding.

        Best effort. If Redis is unreachable the lock expires on its own; the
        only cost is that the retry waits out the TTL, which is a delay rather
        than a failure and not worth failing the sweep over.
        """
        from backend_core.cache import get_redis

        try:
            await get_redis().delete(_LOCK_KEY.format(job_id=job_id))
        except Exception:
            logger.debug("stuck_job_lock_release_failed", extra={"job_id": str(job_id)})


async def reap_stuck_jobs(*, limit: int = 100) -> ReapReport:
    """Entry point for the periodic task (§161)."""
    return await StuckJobReaper().sweep(limit=limit)


__all__ = ["ReapReport", "StuckJobReaper", "reap_stuck_jobs"]
