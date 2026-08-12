"""Two-phase upload service (taskbook §12, P4-T02 / P4-T03).

§12's flow, and why it is shaped this way:

    presign -> browser PUTs straight to storage -> complete

The API never touches the file bytes. That is not an optimisation, it is the
architecture: §116 forbids proxying large media through the API, and a 500 MB
video streamed through a request handler would occupy a worker for the length
of the user's upload. The tradeoff is that the server does not see the content
as it arrives, so **every content check happens in `complete`** — after the
object exists and before the asset is usable.

What that buys, and what it costs:

* A presigned PUT is signed for one exact key and content type, so the client
  cannot choose where the file lands or relabel it.
* The row is written first, in `PENDING`. An upload the browser abandons leaves
  a `PENDING` row and an orphan object, both of which the collector in §163
  reclaims — a leak that is *recorded* rather than invisible.
* Validation is retroactive. A malicious file exists in the bucket between the
  PUT and the `complete` call, which is why the bucket is private (§110), why
  nothing is served from a `PENDING` asset, and why a failed check deletes the
  object rather than leaving it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from anyio import to_thread
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import AssetSourceType, AssetType, UploadStatus
from backend_core.domain.models import MediaAsset, User
from backend_core.errors import AssetInvalidError, NotFoundError, UploadTooLargeError
from backend_core.media.images import probe_image_bytes
from backend_core.media.policy import (
    UploadPolicy,
    header_bytes_needed,
    normalise_mime_type,
    policy_for_mime_type,
    validate_declared_upload,
    verify_content_signature,
)
from backend_core.media.probe import MediaProbeError, MediaStreamInfo, probe_media
from backend_core.observability import get_logger
from backend_core.repositories.media_assets import MediaAssetRepository
from backend_core.storage.base import ObjectMetadata, ObjectNotFoundError, StorageError
from backend_core.storage.keys import belongs_to_workspace, upload_key
from backend_core.storage.s3 import S3ObjectStorage, get_storage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """Everything the browser needs to perform the PUT itself."""

    asset: MediaAsset
    upload_url: str
    expires_in: int

    @property
    def required_headers(self) -> dict[str, str]:
        """Headers the client must send for the signature to validate.

        ``Content-Type`` is part of the signed request, so sending a different
        one makes storage reject the PUT. That is what stops a client from
        signing for a JPEG and then storing an HTML document under the key.
        """
        return {"Content-Type": self.asset.mime_type}


class UploadService:
    """Issues presigned uploads and admits the results into the library."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: S3ObjectStorage | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._storage = storage or get_storage()
        self._assets = MediaAssetRepository(session)

    # -- phase one: presign -------------------------------------------------

    async def presign(
        self,
        *,
        workspace_id: uuid.UUID,
        user: User,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> PresignedUpload:
        """Reserve a key, record a `PENDING` asset and sign a PUT for it.

        Permission is checked by the route via `Permission.ASSET_UPLOAD`; this
        method assumes an authorised caller and concerns itself with what the
        file is allowed to be.
        """
        policy = validate_declared_upload(
            mime_type=mime_type,
            size_bytes=size_bytes,
            filename=filename,
            settings=self._settings,
        )

        object_key = upload_key(workspace_id, policy.extension)
        ttl = self._settings.s3_signed_url_ttl_seconds

        asset = await self._assets.create_pending(
            workspace_id=workspace_id,
            asset_type=policy.asset_type,
            source_type=AssetSourceType.USER_UPLOAD,
            bucket=self._storage.bucket,
            object_key=object_key,
            mime_type=policy.mime_type,
            original_filename=_safe_filename(filename),
            metadata={
                "declared_size_bytes": size_bytes,
                "uploaded_by_user_id": str(user.id),
            },
        )

        # Signing is local computation — no network call — so it does not need
        # a thread despite boto3 being synchronous.
        upload_url = self._storage.presigned_upload_url(
            object_key, policy.mime_type, expires_in=ttl
        )

        logger.info(
            "upload_presigned",
            extra={
                "asset_id": str(asset.id),
                "asset_type": policy.asset_type.value,
                "mime_type": policy.mime_type,
                "declared_size_bytes": size_bytes,
            },
        )
        return PresignedUpload(asset=asset, upload_url=upload_url, expires_in=ttl)

    # -- phase two: complete ------------------------------------------------

    async def complete(
        self,
        *,
        workspace_id: uuid.UUID,
        asset_id: uuid.UUID,
    ) -> MediaAsset:
        """Verify the uploaded object and promote the asset to `READY`.

        Idempotent (§67): completing an already-`READY` asset returns it
        unchanged rather than re-probing, so a client that retries after a
        dropped response does not pay for the work twice or get a 409 for
        having succeeded.
        """
        asset = await self._assets.get(workspace_id, asset_id)
        if asset is None:
            raise NotFoundError("That upload does not exist.")

        if asset.upload_status is UploadStatus.READY:
            return asset

        if asset.upload_status is UploadStatus.FAILED:
            raise AssetInvalidError(
                "That upload already failed validation. Start a new upload.",
                details={"asset_id": str(asset_id)},
            )

        # Defence in depth: the key came from our own database, but a bug that
        # let a foreign key be stored would otherwise become a cross-tenant
        # read here (§61).
        if not belongs_to_workspace(asset.object_key, workspace_id):
            raise NotFoundError("That upload does not exist.")

        try:
            await self._verify_and_populate(asset)
        except (AssetInvalidError, UploadTooLargeError):
            await self._reject(asset)
            raise

        asset.upload_status = UploadStatus.READY
        await self._session.flush()

        logger.info(
            "upload_completed",
            extra={
                "asset_id": str(asset.id),
                "asset_type": asset.asset_type.value,
                "size_bytes": asset.size_bytes,
                "duration_ms": asset.duration_ms,
            },
        )
        return asset

    # -- verification -------------------------------------------------------

    async def _verify_and_populate(self, asset: MediaAsset) -> None:
        """Run every §12 check against the object that actually landed."""
        stored = await self._head(asset.object_key)

        policy = policy_for_mime_type(asset.mime_type, self._settings)
        self._check_size(stored, policy)
        await self._check_signature(asset.object_key, policy)

        asset.size_bytes = stored.size_bytes
        if stored.etag:
            # S3 ETags arrive quoted, and for a multipart upload the value is
            # a digest-of-digests rather than a content hash. Kept as an opaque
            # storage identifier, never presented as a checksum.
            asset.asset_metadata = {**asset.asset_metadata, "etag": stored.etag.strip('"')}

        # A client that declares `image/jpeg` and stores something with a
        # different `Content-Type` has bypassed the signature — which should be
        # impossible, so it is treated as an attack rather than a mismatch.
        if normalise_mime_type(stored.content_type) != policy.mime_type:
            raise AssetInvalidError(
                "The stored file does not match the type it was uploaded as.",
                details={"declared": policy.mime_type, "stored": stored.content_type},
            )

        if policy.asset_type is AssetType.IMAGE:
            await self._populate_image(asset, policy)
        else:
            await self._populate_video(asset)

    def _check_size(self, stored: ObjectMetadata, policy: UploadPolicy) -> None:
        """Enforce the limit against the object's real length.

        The size checked at presign time was the client's claim. This is the
        one that counts — a presigned PUT does not itself cap the body.
        """
        if stored.size_bytes <= 0:
            raise AssetInvalidError("The uploaded file is empty.")
        if stored.size_bytes > policy.max_bytes:
            raise UploadTooLargeError(
                f"That file is larger than the {policy.max_bytes // (1024 * 1024)} MB limit.",
                details={"size_bytes": stored.size_bytes, "max_bytes": policy.max_bytes},
            )

    async def _check_signature(self, object_key: str, policy: UploadPolicy) -> None:
        """Match the container header against the declared type."""
        header = await self._read_prefix(object_key, header_bytes_needed())
        verify_content_signature(header, policy)

    async def _populate_image(self, asset: MediaAsset, policy: UploadPolicy) -> None:
        """Decode the image for dimensions, and hash it while it is in hand."""
        data = await self._get_bytes(asset.object_key)
        metadata = probe_image_bytes(data, mime_type=policy.mime_type, settings=self._settings)

        asset.width = metadata.width
        asset.height = metadata.height
        # SHA-256 over bytes already in memory: no extra transfer, and §12 asks
        # for a file hash. Video takes a different path — see below.
        asset.checksum = hashlib.sha256(data).hexdigest()
        asset.asset_metadata = {
            **asset.asset_metadata,
            "image_format": metadata.format,
            "color_mode": metadata.mode,
        }

    async def _populate_video(self, asset: MediaAsset) -> None:
        """Probe the container over a presigned URL, without downloading it.

        ffprobe fetches the header with ranged requests, so a 500 MB upload is
        inspected in a few hundred kilobytes of traffic and the API never holds
        the file (§116).

        No checksum is recorded here on purpose: hashing would mean streaming
        the whole object through this process, which is exactly what the
        presigned flow exists to avoid. Video hashing belongs to the ingest
        worker once the job system lands (PHASE 9); until then `checksum` is
        null for video and the storage ETag is kept in `metadata`.
        """
        url = self._storage.presigned_download_url(
            asset.object_key, expires_in=self._settings.media_probe_timeout_seconds + 30
        )

        try:
            info: MediaStreamInfo = await to_thread.run_sync(
                lambda: probe_media(url, settings=self._settings)
            )
        except MediaProbeError as exc:
            raise AssetInvalidError("The video file could not be read.") from exc

        if not info.has_video:
            raise AssetInvalidError("The file contains no video track.")

        if info.duration_ms is None:
            raise AssetInvalidError("The video has no readable duration.")

        max_ms = self._settings.max_upload_video_duration_seconds * 1000
        if info.duration_ms > max_ms:
            raise AssetInvalidError(
                f"The video is longer than the "
                f"{self._settings.max_upload_video_duration_seconds} second limit.",
                details={"duration_ms": info.duration_ms, "max_duration_ms": max_ms},
            )

        asset.width = info.width
        asset.height = info.height
        asset.duration_ms = info.duration_ms
        asset.fps = info.fps
        asset.codec = info.video_codec
        asset.asset_metadata = {
            **asset.asset_metadata,
            "container_format": info.container_format,
            "audio_codec": info.audio_codec,
            "bit_rate": info.bit_rate,
        }

    async def _reject(self, asset: MediaAsset) -> None:
        """Mark the asset failed and remove the object it points at.

        **The status is written in its own transaction, on purpose.** The
        rejection is raised as an error, and the request-scoped session rolls
        back on any exception — so a `FAILED` written through `self._session`
        is undone on its way out, leaving a `PENDING` row pointing at an object
        this method has already deleted. That state is worse than either
        outcome: the client's retry finds nothing to complete, and the GC has
        nothing to distinguish an abandoned upload from a rejected one.

        The failure record is therefore committed independently, the way an
        audit entry is: it describes something that happened, and the fact that
        the request failed is precisely why it must survive.

        Deleting the object is safe and correct — it was written by this
        workspace under a key this server issued, and has just been proven
        invalid. The delete is best-effort and runs second: if storage is
        unavailable the asset is still marked `FAILED` and §163's collector
        reclaims the object later. The reverse order could lose the record.
        """
        async with get_async_sessionmaker()() as session:
            await session.execute(
                update(MediaAsset)
                .where(MediaAsset.id == asset.id)
                .values(upload_status=UploadStatus.FAILED)
            )
            await session.commit()

        try:
            await to_thread.run_sync(self._storage.delete, asset.object_key)
        except StorageError:
            logger.warning(
                "rejected_upload_not_deleted",
                extra={"asset_id": str(asset.id)},
                exc_info=True,
            )

    # -- storage, off the event loop ---------------------------------------
    #
    # boto3 is synchronous. Called directly from an async handler it would
    # block the event loop for the length of a network round trip, stalling
    # every other request in the process.

    async def _head(self, object_key: str) -> ObjectMetadata:
        try:
            return await to_thread.run_sync(self._storage.head, object_key)
        except ObjectNotFoundError as exc:
            raise AssetInvalidError(
                "No file was uploaded for this asset.", details={"object_key": object_key}
            ) from exc

    async def _read_prefix(self, object_key: str, length: int) -> bytes:
        return await to_thread.run_sync(self._storage.read_prefix, object_key, length)

    async def _get_bytes(self, object_key: str) -> bytes:
        return await to_thread.run_sync(self._storage.get_bytes, object_key)

    # -- reads --------------------------------------------------------------

    async def list_assets(
        self,
        *,
        workspace_id: uuid.UUID,
        asset_type: AssetType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaAsset]:
        return await self._assets.list_for_workspace(
            workspace_id, asset_type=asset_type, limit=limit, offset=offset
        )

    async def get_asset(self, *, workspace_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset:
        asset = await self._assets.get(workspace_id, asset_id)
        if asset is None:
            raise NotFoundError("That asset does not exist.")
        return asset

    def download_url(self, asset: MediaAsset) -> str:
        """A short-lived read URL. Buckets are private, so this is the only way in (§110)."""
        return self._storage.presigned_download_url(asset.object_key)


def _safe_filename(filename: str) -> str | None:
    """Keep the user's filename for display, stripped of anything path-like.

    It is never used to address storage (§11 — the server names the file), but
    it *is* rendered in the UI and echoed in API responses, so directory
    separators and control characters come out here rather than in whichever
    consumer forgets to escape them.
    """
    cleaned = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in cleaned if character.isprintable()).strip()
    return cleaned[:255] or None


def public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Fields safe to return to a client.

    `metadata` accumulates internal detail — storage ETags, probe output — that
    a UI has no use for and that describes infrastructure. Only the media
    facts are exposed.
    """
    public_keys = {"image_format", "color_mode", "container_format", "audio_codec"}
    return {key: value for key, value in metadata.items() if key in public_keys}
