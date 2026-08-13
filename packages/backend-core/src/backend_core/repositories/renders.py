"""Reads over renders, voiceovers, subtitles and quality checks (§33, §34, §37)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import RenderStatus
from backend_core.domain.models import (
    MediaAsset,
    QualityCheck,
    Render,
    SubtitleTrack,
    VoiceoverTrack,
)


class RenderRepository:
    """Queries the composition side of a project."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID
    ) -> Render | None:
        result = await self._session.execute(
            select(Render).where(
                Render.id == render_id,
                Render.workspace_id == workspace_id,
                Render.project_id == project_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[Render]:
        result = await self._session.execute(
            select(Render)
            .where(Render.workspace_id == workspace_id, Render.project_id == project_id)
            .order_by(Render.version.desc())
        )
        return list(result.scalars().all())

    async def latest_completed(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> Render | None:
        """The newest render that actually produced a file.

        Used by the download endpoint. "Latest" alone would hand back a render
        still in progress, whose `output_asset_id` is null.
        """
        result = await self._session.execute(
            select(Render)
            .where(
                Render.workspace_id == workspace_id,
                Render.project_id == project_id,
                Render.status == RenderStatus.COMPLETED,
                Render.output_asset_id.is_not(None),
            )
            .order_by(Render.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def next_version(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.max(Render.version)).where(
                Render.workspace_id == workspace_id, Render.project_id == project_id
            )
        )
        return int(result.scalar() or 0) + 1

    async def latest_voiceover(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> VoiceoverTrack | None:
        result = await self._session.execute(
            select(VoiceoverTrack)
            .where(
                VoiceoverTrack.workspace_id == workspace_id,
                VoiceoverTrack.project_id == project_id,
            )
            .order_by(VoiceoverTrack.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_subtitles(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> SubtitleTrack | None:
        result = await self._session.execute(
            select(SubtitleTrack)
            .where(
                SubtitleTrack.workspace_id == workspace_id,
                SubtitleTrack.project_id == project_id,
            )
            .order_by(SubtitleTrack.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_quality_checks(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, render_id: uuid.UUID | None = None
    ) -> list[QualityCheck]:
        query = select(QualityCheck).where(
            QualityCheck.workspace_id == workspace_id,
            QualityCheck.project_id == project_id,
        )
        if render_id is not None:
            query = query.where(QualityCheck.render_id == render_id)
        result = await self._session.execute(query.order_by(QualityCheck.created_at.desc()))
        return list(result.scalars().all())

    async def asset(self, workspace_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset | None:
        """Fetch an asset, scoped to the workspace.

        Scoped rather than a bare `session.get`: a render row names an asset id,
        and following it without re-checking the tenant would let a corrupted
        row read across workspaces.
        """
        result = await self._session.execute(
            select(MediaAsset).where(
                MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
            )
        )
        return result.scalar_one_or_none()


__all__ = ["RenderRepository"]
