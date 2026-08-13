"""Data access for generation jobs and provider attempts (§10.15, §10.16)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import JobStatus, JobType
from backend_core.domain.models import GenerationJob, ProviderJob


class JobRepository:
    """Reads and writes for the job aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> GenerationJob | None:
        result = await self._session.execute(
            select(GenerationJob).where(
                GenerationJob.id == job_id,
                GenerationJob.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, workspace_id: uuid.UUID, key: str
    ) -> GenerationJob | None:
        """§23: a repeated request must return the original job.

        Read before insert, and the unique constraint catches the race the read
        cannot — two simultaneous requests both find nothing and both insert,
        and the database refuses the second.
        """
        result = await self._session.execute(
            select(GenerationJob).where(
                GenerationJob.workspace_id == workspace_id,
                GenerationJob.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        job_type: JobType | None = None,
        limit: int = 100,
    ) -> list[GenerationJob]:
        query = select(GenerationJob).where(
            GenerationJob.workspace_id == workspace_id,
            GenerationJob.project_id == project_id,
        )
        if job_type is not None:
            query = query.where(GenerationJob.job_type == job_type)
        result = await self._session.execute(
            query.order_by(GenerationJob.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_shot(
        self, workspace_id: uuid.UUID, shot_id: uuid.UUID
    ) -> list[GenerationJob]:
        result = await self._session.execute(
            select(GenerationJob)
            .where(
                GenerationJob.workspace_id == workspace_id,
                GenerationJob.shot_id == shot_id,
            )
            .order_by(GenerationJob.created_at.desc())
        )
        return list(result.scalars().all())

    async def find_stuck(self, *, older_than_seconds: int, limit: int = 100) -> list[GenerationJob]:
        """Jobs that have been working too long (§161).

        Scans the partial index on active jobs, so this stays cheap enough to
        run on a schedule rather than becoming the thing nobody enabled.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        result = await self._session.execute(
            select(GenerationJob)
            .where(
                GenerationJob.status.in_(
                    [JobStatus.QUEUED, JobStatus.SUBMITTED, JobStatus.PROCESSING]
                ),
                GenerationJob.started_at.is_not(None),
                GenerationJob.started_at < cutoff,
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_active(self, workspace_id: uuid.UUID) -> int:
        """How much work a workspace has in flight, for fair-use limits (§121)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(GenerationJob)
            .where(
                GenerationJob.workspace_id == workspace_id,
                GenerationJob.status.in_(
                    [JobStatus.CREATED, JobStatus.QUEUED, JobStatus.SUBMITTED, JobStatus.PROCESSING]
                ),
            )
        )
        return int(result.scalar_one())

    # -- provider attempts --------------------------------------------------

    async def latest_attempt(self, job_id: uuid.UUID) -> ProviderJob | None:
        result = await self._session.execute(
            select(ProviderJob)
            .where(ProviderJob.generation_job_id == job_id)
            .order_by(ProviderJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_attempts(self, job_id: uuid.UUID) -> list[ProviderJob]:
        result = await self._session.execute(
            select(ProviderJob)
            .where(ProviderJob.generation_job_id == job_id)
            .order_by(ProviderJob.created_at.asc())
        )
        return list(result.scalars().all())

    async def touch_polled(self, attempt_id: uuid.UUID) -> None:
        await self._session.execute(
            update(ProviderJob)
            .where(ProviderJob.id == attempt_id)
            .values(last_polled_at=datetime.now(UTC))
        )
