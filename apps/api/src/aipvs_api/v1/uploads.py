"""Upload and media asset endpoints (taskbook §12, P4-T02 / P4-T03).

The file bytes never appear in this module. `presign` hands the browser a URL
it PUTs to directly and `complete` inspects what landed — §116's rule that
large media does not pass through the API, expressed as two small JSON
endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import CurrentUser, SessionDep, rate_limited, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.config import get_settings
from backend_core.domain.enums import AssetType, Permission
from backend_core.domain.models import MediaAsset
from backend_core.media.policy import supported_upload_mime_types
from backend_core.services.uploads import UploadService, public_metadata

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["media"])


# --- response models -------------------------------------------------------


class MediaAssetResponse(BaseModel):
    """An asset as the client sees it.

    Deliberately does **not** expose `bucket` or `object_key`. Those are
    infrastructure: revealing them tells a client where files live and invites
    code that constructs storage paths instead of asking for a signed URL.
    """

    id: uuid.UUID
    asset_type: AssetType
    upload_status: str
    mime_type: str
    original_filename: str | None
    size_bytes: int | None
    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    codec: str | None
    checksum: str | None
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, asset: MediaAsset) -> MediaAssetResponse:
        return cls(
            id=asset.id,
            asset_type=asset.asset_type,
            upload_status=asset.upload_status.value,
            mime_type=asset.mime_type,
            original_filename=asset.original_filename,
            size_bytes=asset.size_bytes,
            width=asset.width,
            height=asset.height,
            duration_ms=asset.duration_ms,
            fps=asset.fps,
            codec=asset.codec,
            checksum=asset.checksum,
            metadata=public_metadata(asset.asset_metadata),
            created_at=asset.created_at,
        )


class MediaAssetDetailResponse(MediaAssetResponse):
    """An asset plus a short-lived URL to fetch it (§110)."""

    download_url: str

    @classmethod
    def of_with_url(cls, asset: MediaAsset, download_url: str) -> MediaAssetDetailResponse:
        return cls(**MediaAssetResponse.of(asset).model_dump(), download_url=download_url)


class PresignResponse(BaseModel):
    """Instructions for the browser's direct-to-storage PUT."""

    asset: MediaAssetResponse
    upload_url: str = Field(description="Presigned URL. Send the file body here with PUT.")
    method: str = Field(default="PUT", description="HTTP method the signature covers.")
    headers: dict[str, str] = Field(
        description=(
            "Headers that must accompany the PUT. They are part of the signature, "
            "so omitting or changing one makes storage reject the upload."
        )
    )
    expires_in: int = Field(description="Seconds until the URL stops working.")


class UploadConfigResponse(BaseModel):
    """What this deployment accepts.

    Served so the file picker and the validator agree by construction. A
    hardcoded client-side list drifts from the server's and turns a policy
    change into a confusing rejection after the transfer.
    """

    mime_types: list[str]
    max_image_bytes: int
    max_video_bytes: int
    max_image_megapixels: int
    max_video_duration_seconds: int


# --- requests --------------------------------------------------------------


class PresignRequest(ApiRequest):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=255)
    size_bytes: int = Field(
        gt=0,
        description=(
            "The file's size as the client sees it. Checked again against the "
            "stored object at completion — this is an early rejection, not the "
            "authoritative limit."
        ),
    )


# --- routes ----------------------------------------------------------------


@router.get(
    "/uploads/config",
    response_model=UploadConfigResponse,
    summary="What may be uploaded",
    dependencies=[require_permission(Permission.WORKSPACE_READ)],
)
async def upload_config() -> UploadConfigResponse:
    """The accepted formats and limits for this deployment."""
    settings = get_settings()
    mime_types = supported_upload_mime_types(settings)
    return UploadConfigResponse(
        mime_types=list(mime_types),
        max_image_bytes=settings.max_upload_image_bytes,
        max_video_bytes=settings.max_upload_video_bytes,
        max_image_megapixels=settings.max_upload_image_megapixels,
        max_video_duration_seconds=settings.max_upload_video_duration_seconds,
    )


@router.post(
    "/uploads/presign",
    response_model=PresignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start an upload",
    dependencies=[require_permission(Permission.ASSET_UPLOAD), rate_limited("presign")],
)
async def presign_upload(
    workspace_id: uuid.UUID,
    payload: PresignRequest,
    user: CurrentUser,
    session: SessionDep,
) -> PresignResponse:
    """Reserve a key and sign a PUT the browser performs itself.

    Creates the asset in `PENDING`. Nothing is served from it until
    `complete` has verified the object.
    """
    presigned = await UploadService(session).presign(
        workspace_id=workspace_id,
        user=user,
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    return PresignResponse(
        asset=MediaAssetResponse.of(presigned.asset),
        upload_url=presigned.upload_url,
        headers=presigned.required_headers,
        expires_in=presigned.expires_in,
    )


@router.post(
    "/uploads/{asset_id}/complete",
    response_model=MediaAssetResponse,
    summary="Finish an upload",
    dependencies=[require_permission(Permission.ASSET_UPLOAD), rate_limited("presign")],
)
async def complete_upload(
    workspace_id: uuid.UUID, asset_id: uuid.UUID, session: SessionDep
) -> MediaAssetResponse:
    """Validate the uploaded object and make the asset usable.

    Idempotent: completing an asset that is already `READY` returns it
    unchanged, so a retry after a lost response is safe.
    """
    asset = await UploadService(session).complete(workspace_id=workspace_id, asset_id=asset_id)
    return MediaAssetResponse.of(asset)


@router.get(
    "/assets",
    response_model=list[MediaAssetResponse],
    summary="List media assets",
    dependencies=[require_permission(Permission.WORKSPACE_READ)],
)
async def list_assets(
    workspace_id: uuid.UUID,
    session: SessionDep,
    asset_type: Annotated[AssetType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MediaAssetResponse]:
    """Finished assets in this workspace, newest first."""
    assets = await UploadService(session).list_assets(
        workspace_id=workspace_id, asset_type=asset_type, limit=limit, offset=offset
    )
    return [MediaAssetResponse.of(asset) for asset in assets]


@router.get(
    "/assets/{asset_id}",
    response_model=MediaAssetDetailResponse,
    summary="Get one asset with a download URL",
    dependencies=[require_permission(Permission.ASSET_DOWNLOAD)],
)
async def get_asset(
    workspace_id: uuid.UUID, asset_id: uuid.UUID, session: SessionDep
) -> MediaAssetDetailResponse:
    """One asset, with a short-lived signed URL to fetch its bytes."""
    service = UploadService(session)
    asset = await service.get_asset(workspace_id=workspace_id, asset_id=asset_id)
    return MediaAssetDetailResponse.of_with_url(asset, service.download_url(asset))
