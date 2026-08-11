"""Object storage (taskbook §4.6, §11, P1-T07).

Postgres holds metadata and object keys; the bytes live here (§9). Buckets are
private and every read goes through a short-lived signed URL (§110).
"""

from backend_core.storage.base import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectStorage,
    StorageError,
)
from backend_core.storage.keys import (
    UnsupportedMediaTypeError,
    belongs_to_workspace,
    extension_for_mime_type,
    product_derived_key,
    product_original_key,
    project_audio_key,
    project_subtitle_key,
    project_thumbnail_key,
    render_output_key,
    shot_video_key,
)
from backend_core.storage.s3 import (
    S3ObjectStorage,
    get_storage,
    ping_storage,
    reset_storage_cache,
)

__all__ = [
    "ObjectMetadata",
    "ObjectNotFoundError",
    "ObjectStorage",
    "S3ObjectStorage",
    "StorageError",
    "UnsupportedMediaTypeError",
    "belongs_to_workspace",
    "extension_for_mime_type",
    "get_storage",
    "ping_storage",
    "product_derived_key",
    "product_original_key",
    "project_audio_key",
    "project_subtitle_key",
    "project_thumbnail_key",
    "render_output_key",
    "reset_storage_cache",
    "shot_video_key",
]
