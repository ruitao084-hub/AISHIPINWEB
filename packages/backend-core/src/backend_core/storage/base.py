"""Object storage contract (taskbook §4.6).

Storage is abstracted as an S3-compatible interface so AWS S3, Cloudflare R2,
MinIO, Aliyun OSS and Tencent COS are all deployment choices rather than code
changes. Business code depends on this Protocol, never on ``boto3`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """What the store knows about an object without downloading it."""

    key: str
    size_bytes: int
    content_type: str
    etag: str | None = None


class StorageError(RuntimeError):
    """Object storage failed. Maps to ``STORAGE_ERROR`` in the taxonomy (§65)."""


class ObjectNotFoundError(StorageError):
    """The requested key does not exist."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Object not found: {key}")
        self.key = key


@runtime_checkable
class ObjectStorage(Protocol):
    """Operations the platform needs from an object store."""

    def put_bytes(self, key: str, data: bytes, content_type: str) -> ObjectMetadata:
        """Store raw bytes."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Fetch an object into memory. Only for small objects."""
        ...

    def upload_file(self, source: Path, key: str, content_type: str) -> ObjectMetadata:
        """Stream a local file into the store."""
        ...

    def download_file(self, key: str, destination: Path) -> Path:
        """Stream an object to a local path.

        Streaming rather than returning bytes matters for video: a render can
        pull several hundred megabytes and must not hold it in memory (§116).
        """
        ...

    def delete(self, key: str) -> None:
        """Remove an object. Succeeds if it is already gone."""
        ...

    def exists(self, key: str) -> bool:
        """Whether the key is present."""
        ...

    def head(self, key: str) -> ObjectMetadata:
        """Metadata without transferring the body."""
        ...

    def presigned_upload_url(
        self, key: str, content_type: str, expires_in: int | None = None
    ) -> str:
        """URL letting a browser PUT straight to storage (§12)."""
        ...

    def presigned_download_url(self, key: str, expires_in: int | None = None) -> str:
        """Short-lived read URL. Buckets stay private (§110)."""
        ...
