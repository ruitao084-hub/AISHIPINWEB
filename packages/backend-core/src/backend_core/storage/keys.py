"""Object key construction (taskbook §11).

Two rules drive every function here:

1. **Workspace isolation.** Every key begins ``workspaces/{workspace_id}/`` so
   a bucket-level prefix policy can enforce tenant separation, and a bug in one
   query cannot reach another tenant's objects.
2. **The server names the file.** §11 is explicit that a user-supplied filename
   is never trusted. Names are generated UUIDs and the extension comes from a
   whitelist keyed on the validated MIME type — never from the upload. This
   closes path traversal (``../``), null bytes, and double extensions
   (``x.jpg.svg``) in one move, rather than trying to sanitise them.

The original filename is still worth keeping for display, but it belongs in
``media_assets.original_filename`` (§10.17), not in the key.
"""

from __future__ import annotations

import re
from typing import Final
from uuid import UUID, uuid4

# Only formats the platform actually accepts (§12, §151). Anything not listed
# is rejected upstream rather than given a fallback extension.
_MIME_TO_EXTENSION: Final[dict[str, str]] = {
    # Images
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    # Video
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    # Audio
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
    # Text
    "application/x-subrip": "srt",
    "text/vtt": "vtt",
}

_SAFE_EXTENSION: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{1,8}$")


class UnsupportedMediaTypeError(ValueError):
    """Raised when a MIME type has no approved extension."""

    def __init__(self, mime_type: str) -> None:
        super().__init__(f"Unsupported media type: {mime_type!r}")
        self.mime_type = mime_type


def extension_for_mime_type(mime_type: str) -> str:
    """Map a validated MIME type to its canonical extension.

    Raises rather than guessing: an unknown type means validation upstream let
    something through, and inventing an extension would hide that.
    """
    normalised = mime_type.split(";")[0].strip().lower()
    extension = _MIME_TO_EXTENSION.get(normalised)
    if extension is None:
        raise UnsupportedMediaTypeError(mime_type)
    return extension


def _validate_extension(extension: str) -> str:
    """Reject anything that is not a short, lowercase, alphanumeric suffix."""
    candidate = extension.lstrip(".").lower()
    if not _SAFE_EXTENSION.match(candidate):
        raise ValueError(f"Unsafe object key extension: {extension!r}")
    return candidate


def _workspace_prefix(workspace_id: UUID) -> str:
    return f"workspaces/{workspace_id}"


def product_original_key(workspace_id: UUID, product_id: UUID, extension: str) -> str:
    """Key for an image exactly as the user uploaded it."""
    ext = _validate_extension(extension)
    return f"{_workspace_prefix(workspace_id)}/products/{product_id}/originals/{uuid4()}.{ext}"


def product_derived_key(workspace_id: UUID, product_id: UUID, extension: str) -> str:
    """Key for a processed variant: thumbnail, cutout, normalised copy (§28)."""
    ext = _validate_extension(extension)
    return f"{_workspace_prefix(workspace_id)}/products/{product_id}/derived/{uuid4()}.{ext}"


def shot_video_key(workspace_id: UUID, project_id: UUID, shot_id: UUID) -> str:
    """Key for a generated shot video.

    A fresh UUID per call is deliberate: regenerating a shot must never
    overwrite the previous take (§144). History is what lets a user pick a
    different result later.
    """
    return f"{_workspace_prefix(workspace_id)}/projects/{project_id}/shots/{shot_id}/{uuid4()}.mp4"


def project_audio_key(workspace_id: UUID, project_id: UUID, extension: str = "wav") -> str:
    """Key for voiceover or background audio."""
    ext = _validate_extension(extension)
    return f"{_workspace_prefix(workspace_id)}/projects/{project_id}/audio/{uuid4()}.{ext}"


def project_subtitle_key(workspace_id: UUID, project_id: UUID, extension: str = "srt") -> str:
    """Key for a subtitle track."""
    ext = _validate_extension(extension)
    return f"{_workspace_prefix(workspace_id)}/projects/{project_id}/subtitles/{uuid4()}.{ext}"


def render_output_key(workspace_id: UUID, project_id: UUID, render_id: UUID) -> str:
    """Key for a final render.

    Named by ``render_id`` rather than a random UUID because a render *is* the
    identity of its output — re-running one replaces its own artefact, and
    every distinct output already has its own ``Render`` row (§10.23).
    """
    return f"{_workspace_prefix(workspace_id)}/projects/{project_id}/renders/{render_id}.mp4"


def project_thumbnail_key(workspace_id: UUID, project_id: UUID) -> str:
    """Key for a poster frame (§143)."""
    return f"{_workspace_prefix(workspace_id)}/projects/{project_id}/thumbnails/{uuid4()}.jpg"


def belongs_to_workspace(object_key: str, workspace_id: UUID) -> bool:
    """Check a key is inside a workspace before acting on it.

    Defence in depth for any path where a key arrives from a request rather
    than from the database (§61 object key isolation).
    """
    return object_key.startswith(f"{_workspace_prefix(workspace_id)}/")
