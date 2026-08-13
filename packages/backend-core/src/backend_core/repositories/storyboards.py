"""Data access for storyboards, shots and shot references (§10.12-§10.14)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend_core.domain.enums import StoryboardStatus
from backend_core.domain.models import Shot, ShotReference, Storyboard


class StoryboardRepository:
    """Reads and writes for the storyboard aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- storyboards --------------------------------------------------------

    async def get(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, storyboard_id: uuid.UUID
    ) -> Storyboard | None:
        result = await self._session.execute(
            select(Storyboard).where(
                Storyboard.id == storyboard_id,
                Storyboard.project_id == project_id,
                Storyboard.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[Storyboard]:
        result = await self._session.execute(
            select(Storyboard)
            .where(
                Storyboard.workspace_id == workspace_id,
                Storyboard.project_id == project_id,
            )
            .order_by(Storyboard.version.desc())
        )
        return list(result.scalars().all())

    async def approved(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Storyboard | None:
        """The one storyboard PHASE 9 will generate shots from."""
        result = await self._session.execute(
            select(Storyboard).where(
                Storyboard.workspace_id == workspace_id,
                Storyboard.project_id == project_id,
                Storyboard.status == StoryboardStatus.APPROVED,
            )
        )
        return result.scalar_one_or_none()

    async def next_version(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(Storyboard.version), 0)).where(
                Storyboard.workspace_id == workspace_id,
                Storyboard.project_id == project_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def supersede(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, *, keep: uuid.UUID
    ) -> None:
        await self._session.execute(
            update(Storyboard)
            .where(
                Storyboard.workspace_id == workspace_id,
                Storyboard.project_id == project_id,
                Storyboard.id != keep,
                Storyboard.status != StoryboardStatus.SUPERSEDED,
            )
            .values(status=StoryboardStatus.SUPERSEDED)
        )

    # -- shots --------------------------------------------------------------

    async def list_shots(self, workspace_id: uuid.UUID, storyboard_id: uuid.UUID) -> list[Shot]:
        """A storyboard's shots in sequence, with their references loaded.

        `selectinload` because the caller always needs the references to render
        a shot, and models use ``lazy="raise"`` — a lazy load here would raise
        rather than issue the N queries it would otherwise cost.
        """
        result = await self._session.execute(
            select(Shot)
            .options(selectinload(Shot.references))
            .where(
                Shot.workspace_id == workspace_id,
                Shot.storyboard_id == storyboard_id,
            )
            .order_by(Shot.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def get_shot(
        self, workspace_id: uuid.UUID, storyboard_id: uuid.UUID, shot_id: uuid.UUID
    ) -> Shot | None:
        result = await self._session.execute(
            select(Shot)
            .options(selectinload(Shot.references))
            .where(
                Shot.id == shot_id,
                Shot.storyboard_id == storyboard_id,
                Shot.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def sum_shot_durations(self, workspace_id: uuid.UUID, storyboard_id: uuid.UUID) -> float:
        """The storyboard's real total, from the shots themselves.

        `Storyboard.total_duration_seconds` is denormalised; this is the source
        of truth it is recomputed from after any edit.
        """
        result = await self._session.execute(
            select(func.coalesce(func.sum(Shot.duration_seconds), 0.0)).where(
                Shot.workspace_id == workspace_id,
                Shot.storyboard_id == storyboard_id,
            )
        )
        return round(float(result.scalar_one()), 2)

    # -- references ---------------------------------------------------------

    async def list_references(
        self, workspace_id: uuid.UUID, shot_id: uuid.UUID
    ) -> list[ShotReference]:
        result = await self._session.execute(
            select(ShotReference).where(
                ShotReference.workspace_id == workspace_id,
                ShotReference.shot_id == shot_id,
            )
        )
        return list(result.scalars().all())
