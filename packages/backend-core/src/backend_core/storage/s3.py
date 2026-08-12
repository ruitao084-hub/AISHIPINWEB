"""S3-compatible object storage implementation (P1-T07)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend_core.config import get_settings
from backend_core.storage.base import ObjectMetadata, ObjectNotFoundError, StorageError

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

# Botocore reports "missing" as either of these depending on whether the caller
# has ListBucket permission — treating only one as absence yields confusing
# 500s against least-privilege buckets.
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class S3ObjectStorage:
    """Object storage backed by any S3-compatible endpoint.

    Implements :class:`~backend_core.storage.base.ObjectStorage`.

    Operations are synchronous. That is deliberate rather than an oversight:
    every transfer-heavy call site (media ingestion, rendering) is a worker
    that is synchronous anyway, and the one storage operation the async API
    performs — presigning — is local signing with no network I/O at all, so it
    cannot block the event loop.
    """

    def __init__(self, client: S3Client | None = None, bucket: str | None = None) -> None:
        settings = get_settings()
        self._client: S3Client = client if client is not None else _build_client()
        self._bucket = bucket if bucket is not None else settings.s3_bucket
        self._default_ttl = settings.s3_signed_url_ttl_seconds

    @property
    def bucket(self) -> str:
        return self._bucket

    # -- writes ------------------------------------------------------------

    def put_bytes(self, key: str, data: bytes, content_type: str) -> ObjectMetadata:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to write {key}") from exc
        return ObjectMetadata(key=key, size_bytes=len(data), content_type=content_type)

    def upload_file(self, source: Path, key: str, content_type: str) -> ObjectMetadata:
        if not source.is_file():
            raise StorageError(f"Cannot upload missing file: {source}")
        try:
            self._client.upload_file(
                Filename=str(source),
                Bucket=self._bucket,
                Key=key,
                ExtraArgs={"ContentType": content_type},
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to upload {source} to {key}") from exc
        return ObjectMetadata(key=key, size_bytes=source.stat().st_size, content_type=content_type)

    # -- reads -------------------------------------------------------------

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
        except ClientError as exc:
            raise self._translate(exc, key) from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to read {key}") from exc
        return body

    def read_prefix(self, key: str, length: int) -> bytes:
        """Ranged GET of the first ``length`` bytes.

        ``Range`` is inclusive on both ends, so the last byte index is
        ``length - 1``. A store that ignores the header would return the whole
        object, so the result is truncated defensively rather than trusted.
        """
        if length <= 0:
            raise ValueError("read_prefix length must be positive")
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes=0-{length - 1}"
            )
            body: bytes = response["Body"].read(length)
        except ClientError as exc:
            raise self._translate(exc, key) from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to read prefix of {key}") from exc
        return body[:length]

    def download_file(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(Bucket=self._bucket, Key=key, Filename=str(destination))
        except ClientError as exc:
            raise self._translate(exc, key) from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to download {key}") from exc
        return destination

    def head(self, key: str) -> ObjectMetadata:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise self._translate(exc, key) from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to stat {key}") from exc
        return ObjectMetadata(
            key=key,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=str(response.get("ContentType", "application/octet-stream")),
            etag=response.get("ETag"),
        )

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if self._error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise StorageError(f"Failed to stat {key}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to stat {key}") from exc
        return True

    # -- deletes -----------------------------------------------------------

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to delete {key}") from exc

    # -- presigning --------------------------------------------------------

    def presigned_upload_url(
        self, key: str, content_type: str, expires_in: int | None = None
    ) -> str:
        """Presign a PUT so the browser uploads directly to storage (§12).

        ``ContentType`` is part of the signature, so a client cannot sign for a
        JPEG and then store an HTML document under that key.
        """
        try:
            return self._client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in if expires_in is not None else self._default_ttl,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to presign upload for {key}") from exc

    def presigned_download_url(self, key: str, expires_in: int | None = None) -> str:
        """Presign a GET. Buckets are private; this is the only read path (§110)."""
        try:
            return self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in if expires_in is not None else self._default_ttl,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to presign download for {key}") from exc

    # -- bootstrap ---------------------------------------------------------

    def check_bucket(self) -> bool:
        """Whether the configured bucket is reachable and readable."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            return False
        return True

    def ensure_bucket(self) -> None:
        """Create the bucket when absent. For local development and tests only.

        Production buckets are provisioned with lifecycle, versioning and
        access policy by infrastructure code, not by the application.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if self._error_code(exc) not in _NOT_FOUND_CODES:
                raise StorageError(f"Failed to inspect bucket {self._bucket}") from exc
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except (ClientError, BotoCoreError) as create_exc:
                raise StorageError(f"Failed to create bucket {self._bucket}") from create_exc

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _error_code(exc: ClientError) -> str:
        return str(exc.response.get("Error", {}).get("Code", ""))

    def _translate(self, exc: ClientError, key: str) -> StorageError:
        if self._error_code(exc) in _NOT_FOUND_CODES:
            return ObjectNotFoundError(key)
        return StorageError(f"Storage operation failed for {key}")


def _build_client() -> S3Client:
    """Construct a boto3 S3 client from settings."""
    settings = get_settings()
    client_kwargs: dict[str, Any] = {
        "region_name": settings.s3_region,
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    }
    if settings.s3_endpoint:
        client_kwargs["endpoint_url"] = settings.s3_endpoint

    access_key = settings.s3_access_key_id.get_secret_value()
    secret_key = settings.s3_secret_access_key.get_secret_value()
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key
    # With no explicit credentials boto3 falls back to the standard chain
    # (instance role, IRSA, environment) — which is how production should run.

    client: S3Client = boto3.client("s3", **client_kwargs)
    return client


@lru_cache(maxsize=1)
def get_storage() -> S3ObjectStorage:
    """Return the process-wide storage client."""
    return S3ObjectStorage()


def ping_storage() -> bool:
    """Return whether the bucket is reachable. Used by the readiness probe (§70)."""
    try:
        return get_storage().check_bucket()
    except Exception:
        return False


def reset_storage_cache() -> None:
    """Drop the cached client. Tests only."""
    get_storage.cache_clear()
