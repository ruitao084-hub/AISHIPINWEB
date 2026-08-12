"""Media asset data access (§10.17).

Every method takes a ``workspace_id`` and filters on it. There is deliberately
no ``get_by_id`` that omits the scope: an asset id arriving from a request is
attacker-controlled, and a lookup that trusts it is a cross-tenant read waiting
to happen (§61).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import AssetSourceType, AssetType, UploadStatus
from backend_core.domain.models import MediaAsset


class MediaAssetRepository:
    """Reads and writes for :class:`MediaAsset`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        workspace_id: uuid.UUID,
        asset_type: AssetType,
        source_type: AssetSourceType,
        bucket: str,
        object_key: str,
        mime_type: str,
        original_filename: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> MediaAsset:
        """Record an upload that has been presigned but not yet transferred.

        The row exists before the bytes do. That ordering is what makes the
        handshake safe: the object key is chosen and persisted by the server
        (§11), so `complete` verifies a key it issued rather than one the
        client hands back.
        """
        asset = MediaAsset(
            workspace_id=workspace_id,
            asset_type=asset_type,
            source_type=source_type,
            upload_status=UploadStatus.PENDING,
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
            original_filename=original_filename,
            asset_metadata=metadata or {},
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def get(self, workspace_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset | None:
        """Fetch one asset within a workspace. Soft-deleted rows are invisible."""
        result = await self._session.execute(
            select(MediaAsset).where(
                MediaAsset.id == asset_id,
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        asset_type: AssetType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaAsset]:
        """The workspace's finished assets, newest first.

        `PENDING` and `FAILED` rows are excluded: an asset the browser never
        finished uploading is bookkeeping, not something to show in a library.
        """
        statement = (
            select(MediaAsset)
            .where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.upload_status == UploadStatus.READY,
                MediaAsset.deleted_at.is_(None),
            )
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if asset_type is not None:
            statement = statement.where(MediaAsset.asset_type == asset_type)

        result = await self._session.execute(statement)
        return list(result.scalars().all())
