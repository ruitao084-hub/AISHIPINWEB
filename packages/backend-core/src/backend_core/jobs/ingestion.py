"""Media ingestion from a provider's temporary URL (§27, P9-T10).

§27 is unambiguous and worth quoting:

    不得把 Provider 临时 URL 当永久地址。

Never treat a provider's temporary URL as permanent. They expire — often within
hours — and a project whose finished video is a dead link is a project the
customer paid for and cannot use.

So every provider result goes through the same six steps §27 lists: download,
verify the MIME type, probe it, compute metadata, store it in our own object
storage, create a `MediaAsset`. Only then may anything reference it.

The validation is not ceremony. A provider that returns an HTML error page with
a 200 is a real failure mode, and without the magic-byte check it would be
stored as an .mp4 and discovered at render time.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import AssetSourceType, AssetType, UploadStatus
from backend_core.domain.models import MediaAsset
from backend_core.errors import AppError, ErrorCode
from backend_core.media.policy import policy_for_mime_type, verify_content_signature
from backend_core.media.probe import probe_media
from backend_core.observability import get_logger
from backend_core.storage.keys import project_audio_key, shot_video_key
from backend_core.storage.s3 import get_storage

logger = get_logger(__name__)

#: Read ceiling for a downloaded clip. A provider returning something enormous
#: is a provider malfunctioning, and streaming it to disk unbounded is how one
#: bad response fills a worker's volume.
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


class IngestionError(AppError):
    """The provider's result could not be fetched or was not what it claimed."""

    code = ErrorCode.STORAGE_ERROR
    http_status = 502
    default_message = "The generated media could not be stored."


async def ingest_provider_media(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_url: str,
    asset_type: AssetType,
    expected_mime_type: str,
    project_id: uuid.UUID,
    shot_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    filename: str | None = None,
) -> MediaAsset:
    """Download, verify and re-host one provider result (§27).

    Returns a `READY` `MediaAsset`. Raises `IngestionError` on anything that
    goes wrong — which §24 classifies as retryable, since a failed download is
    usually transient and the provider's URL may still be alive.
    """
    resolved = settings or get_settings()
    storage = get_storage()
    policy = policy_for_mime_type(expected_mime_type)

    with tempfile.TemporaryDirectory(prefix="aipvs-ingest-") as workspace:
        local = Path(workspace) / (filename or "result.bin")

        size_bytes, checksum = _download(source_url, local)

        # The case this check exists for: an HTML error page served with a
        # 200. Without it that lands in storage as an .mp4 and is discovered at
        # render time, hours later.
        with local.open("rb") as reader:
            header = reader.read(64)
        try:
            verify_content_signature(header, policy)
        except Exception as exc:
            raise IngestionError(
                "The provider returned content that is not the media type it claimed.",
                details={"size_bytes": size_bytes, "expected": expected_mime_type},
            ) from exc

        probed = probe_media(local, settings=resolved)

        object_key = (
            shot_video_key(workspace_id, project_id, shot_id)
            if shot_id is not None
            else project_audio_key(workspace_id, project_id, policy.extension)
        )
        storage.upload_file(local, object_key, policy.mime_type)

        asset = MediaAsset(
            workspace_id=workspace_id,
            asset_type=asset_type,
            # §10.17's provenance. Not USER_UPLOAD: retention (§113) and the
            # orphan collector (§163) both treat generated media differently.
            source_type=AssetSourceType.AI_GENERATED,
            bucket=resolved.s3_bucket,
            object_key=object_key,
            original_filename=local.name,
            mime_type=policy.mime_type,
            size_bytes=size_bytes,
            width=probed.width,
            height=probed.height,
            duration_ms=probed.duration_ms,
            fps=probed.fps,
            codec=probed.video_codec or probed.audio_codec,
            # Computed while the bytes were already streaming past, which is
            # why generated media can carry a checksum where uploads cannot
            # (technical debt #9) — the worker holds the file, the API does not.
            checksum=checksum,
            upload_status=UploadStatus.READY,
            asset_metadata={
                "source": "provider",
                # The host only. A provider's result path routinely carries a
                # signed token, and §62 keeps credentials out of stored rows.
                "source_host": _host(source_url),
                "container_format": probed.container_format,
                "has_audio": probed.has_audio,
            },
        )
        session.add(asset)
        await session.flush()

        logger.info(
            "provider_media_ingested",
            extra={
                "asset_id": str(asset.id),
                "size_bytes": size_bytes,
                "duration_ms": probed.duration_ms,
                "mime_type": policy.mime_type,
            },
        )
        return asset


def _download(source_url: str, destination: Path) -> tuple[int, str]:
    """Stream a URL to disk, hashing as it goes.

    Streaming rather than `.read()`: a generated clip can be hundreds of
    megabytes, and holding one in memory per concurrent worker is how a worker
    box runs out of RAM at exactly the busiest moment.

    `file://` is accepted because the mock provider returns one (§21 forbids it
    touching the network), and the ingestion path should be the *same* path in
    both cases — a mock that skipped ingestion would leave it untested.
    """
    parsed = urlparse(source_url)
    digest = hashlib.sha256()
    total = 0

    if parsed.scheme == "file":
        from urllib.request import url2pathname

        source = Path(url2pathname(parsed.path))
        if not source.is_file():
            raise IngestionError("The provider's result file does not exist.")
        with source.open("rb") as reader, destination.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                total += len(chunk)
                digest.update(chunk)
                writer.write(chunk)
        return total, digest.hexdigest()

    if parsed.scheme not in ("http", "https"):
        raise IngestionError(f"Refusing to fetch a {parsed.scheme!r} URL.")

    import httpx

    try:
        with httpx.stream("GET", source_url, timeout=120.0, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise IngestionError(
                    "The provider's result URL could not be fetched.",
                    details={"status_code": response.status_code},
                )
            with destination.open("wb") as writer:
                for chunk in response.iter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_BYTES:
                        raise IngestionError("The provider's result is implausibly large.")
                    digest.update(chunk)
                    writer.write(chunk)
    except httpx.RequestError as exc:
        raise IngestionError("The provider's result URL could not be fetched.") from exc

    if total == 0:
        raise IngestionError("The provider's result was empty.")
    return total, digest.hexdigest()


def _host(source_url: str) -> str:
    """Just the host, for diagnostics. The path can carry a signed token (§62)."""
    return urlparse(source_url).netloc or "local"


__all__ = ["IngestionError", "ingest_provider_media"]
