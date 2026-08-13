"""Celery application and task registrations (§25, P9-T04).

§5.1 puts the entrypoint here and the logic in `backend_core.jobs`, so the API
and the worker share one state machine rather than two that drift.

§25's four queues are separate because their work is differently shaped. A
render pins a CPU for minutes; a TTS call is mostly waiting on a network. One
concurrency number for both either starves renders or floods them, so each
queue gets its own worker process with its own `--concurrency`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from celery import Celery

from backend_core.config import get_settings
from backend_core.domain.enums import QueueName
from backend_core.observability import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

celery_app = Celery(
    "aipvs",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Acknowledge after the task finishes, not on receipt. A worker killed
    # mid-generation must leave the job on the queue for someone else — §22's
    # locking makes the redelivery safe, and losing the job would leave a user
    # waiting forever for work nobody is doing.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # A hard ceiling above the per-job budget, so a wedged task is killed
    # rather than holding a worker slot indefinitely (§161).
    task_time_limit=settings.video_job_timeout_seconds + 300,
    task_soft_time_limit=settings.video_job_timeout_seconds + 60,
    task_routes={
        "aipvs.video.generate": {"queue": QueueName.VIDEO.value},
        "aipvs.tts.synthesize": {"queue": QueueName.TTS.value},
        "aipvs.render.compose": {"queue": QueueName.RENDER.value},
        "aipvs.qc.check": {"queue": QueueName.QC.value},
        "aipvs.maintenance.reap_stuck_jobs": {"queue": QueueName.DEFAULT.value},
        "aipvs.batch.dispatch": {"queue": QueueName.DEFAULT.value},
        "aipvs.batch.run_item": {"queue": QueueName.DEFAULT.value},
    },
    # §161's periodic recovery. Every five minutes, which is frequent enough
    # that a user notices a stuck job resolving rather than a support ticket,
    # and rare enough that the scan is invisible against normal load.
    beat_schedule={
        "reap-stuck-jobs": {
            "task": "aipvs.maintenance.reap_stuck_jobs",
            "schedule": 300.0,
            "options": {"queue": QueueName.DEFAULT.value},
        },
    },
)


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter (§24).

    Duplicated from `JobService` rather than reached through a database session
    the task does not otherwise need. Jitter matters here for the same reason:
    twenty tasks re-queued in lockstep rate-limit each other again.
    """
    import random

    base = settings.job_retry_base_delay_seconds * (2 ** max(0, attempt - 1))
    return float(round(min(base, 900) * (0.5 + random.random()), 2))  # noqa: S311


def _run(coroutine: Any) -> Any:
    """Bridge Celery's synchronous task API to our async services.

    `asyncio.run` per task rather than a shared loop: Celery's prefork model
    gives each task an unpredictable thread, and the engine cache is keyed by
    loop precisely so this pattern is safe (PHASE 1).
    """
    return asyncio.run(coroutine)


@celery_app.task(name="aipvs.video.generate", bind=True, max_retries=0)
def generate_video(self: Any, workspace_id: str, job_id: str) -> dict[str, Any]:
    """Run one video generation job (§22).

    `max_retries=0` on the Celery task is deliberate: §24's retry policy lives
    in `JobService`, which knows the difference between a rate limit and a
    policy violation. Celery retrying on top would double the attempts and
    ignore that distinction.
    """
    from backend_core.jobs.runner import JobLockedError, VideoJobRunner

    try:
        outcome = _run(VideoJobRunner().run(uuid.UUID(workspace_id), uuid.UUID(job_id)))
    except JobLockedError:
        # Another worker has it. Not an error: redelivery is expected under
        # `task_acks_late`, and the lock is what makes it harmless.
        logger.info("video_job_already_running", extra={"job_id": job_id})
        return {"job_id": job_id, "status": "LOCKED"}

    if outcome.will_retry:
        # Re-queue after §24's backoff. The delay is derived from the job's own
        # attempt count, which `JobService.fail` has already incremented — so
        # the second attempt waits longer than the first, as exponential
        # backoff requires.
        generate_video.apply_async(
            args=[workspace_id, job_id],
            countdown=_backoff_seconds(outcome.retry_count),
            queue=QueueName.VIDEO.value,
        )

    return {
        "job_id": job_id,
        "status": outcome.status.value,
        "asset_id": str(outcome.asset_id) if outcome.asset_id else None,
    }


@celery_app.task(name="aipvs.tts.synthesize", bind=True, max_retries=0)
def synthesize_speech(self: Any, workspace_id: str, job_id: str) -> dict[str, Any]:
    """Run one TTS job (§30, PHASE 12)."""
    from backend_core.jobs.tts_runner import TTSJobRunner

    outcome = _run(TTSJobRunner().run(uuid.UUID(workspace_id), uuid.UUID(job_id)))
    return {"job_id": job_id, "status": outcome.status.value}


@celery_app.task(name="aipvs.render.compose", bind=True, max_retries=0)
def compose_render(self: Any, workspace_id: str, job_id: str) -> dict[str, Any]:
    """Run one render job (§35, PHASE 13)."""
    from backend_core.jobs.render_runner import RenderJobRunner

    outcome = _run(RenderJobRunner().run(uuid.UUID(workspace_id), uuid.UUID(job_id)))
    return {"job_id": job_id, "status": outcome.status.value}


@celery_app.task(name="aipvs.qc.check", bind=True, max_retries=0)
def run_quality_check(self: Any, workspace_id: str, job_id: str) -> dict[str, Any]:
    """Run one QC job (§32, PHASE 14)."""
    from backend_core.jobs.qc_runner import QCJobRunner

    outcome = _run(QCJobRunner().run(uuid.UUID(workspace_id), uuid.UUID(job_id)))
    return {"job_id": job_id, "status": outcome.status.value}


@celery_app.task(name="aipvs.maintenance.reap_stuck_jobs", bind=True, max_retries=0)
def reap_stuck_jobs(self: Any, limit: int = 100) -> dict[str, Any]:
    """Return abandoned jobs to §106's machine (§161, P16-T15).

    Runs on `beat`, not on a worker's own timer: one scheduler firing this
    means one sweep, where a per-worker timer would have every worker reap the
    same rows and race each other over the same reservations.
    """
    from backend_core.jobs.reaper import reap_stuck_jobs as sweep

    report = _run(sweep(limit=limit))
    return {
        "examined": report.examined,
        "reaped": len(report.reaped),
        "requeued": len(report.requeued),
    }


@celery_app.task(name="aipvs.batch.dispatch", bind=True, max_retries=0)
def dispatch_batch(self: Any, workspace_id: str, batch_id: str) -> dict[str, Any]:
    """Claim and start as many batch items as concurrency allows (§98, P21-T05).

    Re-enqueues itself while work remains rather than looping in one task: a
    task that held a worker slot for a five-hundred-row batch would block the
    default queue for an hour, and a crash would lose the whole run.
    """
    from backend_core.jobs.batch_runner import dispatch

    outcome = _run(dispatch(uuid.UUID(workspace_id), uuid.UUID(batch_id)))

    if outcome.started:
        for item_id in outcome.started:
            run_batch_item.apply_async(
                args=[workspace_id, str(item_id)], queue=QueueName.DEFAULT.value
            )

    if outcome.remaining:
        # Come back when the current tranche has had time to finish. The delay
        # matters: dispatching in a tight loop would spin on a full batch,
        # claiming nothing and burning a worker.
        dispatch_batch.apply_async(
            args=[workspace_id, batch_id], countdown=30, queue=QueueName.DEFAULT.value
        )

    return {
        "batch_id": batch_id,
        "started": len(outcome.started),
        "remaining": outcome.remaining,
    }


@celery_app.task(name="aipvs.batch.run_item", bind=True, max_retries=0)
def run_batch_item(self: Any, workspace_id: str, item_id: str) -> dict[str, Any]:
    """Turn one imported row into a product and project (§98, P21-T05)."""
    from backend_core.jobs.batch_runner import run_item

    outcome = _run(run_item(uuid.UUID(workspace_id), uuid.UUID(item_id)))
    return {"item_id": item_id, "status": outcome.status.value}


__all__ = [
    "celery_app",
    "compose_render",
    "dispatch_batch",
    "generate_video",
    "reap_stuck_jobs",
    "run_batch_item",
    "run_quality_check",
    "synthesize_speech",
]
