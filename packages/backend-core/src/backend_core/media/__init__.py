"""Media validation and inspection (taskbook §12).

Everything an uploaded file must survive before it becomes a `MediaAsset`:
the type whitelist and size limits (:mod:`policy`), image decoding and
dimensions (:mod:`images`), and container inspection for audio and video
(:mod:`probe`).

The split matters because the checks happen at two different moments. §12's
flow presigns a URL *before* any bytes exist, so the declared MIME type and
size can be validated up front, while everything that requires the actual
content — magic bytes, pixel dimensions, duration — can only be checked after
the browser has uploaded and called `complete`.
"""

from backend_core.media.images import ImageMetadata, probe_image_bytes
from backend_core.media.policy import (
    UploadPolicy,
    header_bytes_needed,
    policy_for_mime_type,
    supported_upload_mime_types,
    validate_declared_upload,
    verify_content_signature,
)
from backend_core.media.probe import MediaProbeError, MediaStreamInfo, probe_media

__all__ = [
    "ImageMetadata",
    "MediaProbeError",
    "MediaStreamInfo",
    "UploadPolicy",
    "header_bytes_needed",
    "policy_for_mime_type",
    "probe_image_bytes",
    "probe_media",
    "supported_upload_mime_types",
    "validate_declared_upload",
    "verify_content_signature",
]
