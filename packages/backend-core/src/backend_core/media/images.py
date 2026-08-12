"""Image inspection (taskbook §12 "图片像素", P4-T05).

Decoding an untrusted image is itself the risk, so this module is written
around containing it rather than around reading dimensions — that part is
trivial. Two specific attacks:

* **Decompression bombs.** A 200 KB PNG can declare 60000x60000 and expand to
  tens of gigabytes in memory. The byte-size limit does not bound this at all,
  which is why :attr:`Settings.max_upload_image_megapixels` exists and why the
  dimensions are read from the header *before* anything is decoded.
* **Format confusion.** Pillow will happily open a file whose real format
  differs from its extension. The format it reports is checked against what the
  uploader declared, closing the "JPEG that is really an SVG" case that
  :func:`~backend_core.media.policy.verify_content_signature` starts.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Final

from PIL import Image, UnidentifiedImageError

from backend_core.config import Settings, get_settings
from backend_core.errors import AssetInvalidError

# Pillow's own bomb guard warns at ~89 MP and is a backstop, not the policy;
# ours is configurable and enforced explicitly below. Disabling Pillow's would
# remove a safety net, so it is left alone.

#: Pillow's format name for each MIME type we accept. A file that opens as
#: something else is rejected even if it is a perfectly valid image.
_EXPECTED_FORMATS: Final[dict[str, frozenset[str]]] = {
    "image/jpeg": frozenset({"JPEG", "MPO"}),  # MPO is a multi-picture JPEG from phone cameras
    "image/png": frozenset({"PNG"}),
    "image/webp": frozenset({"WEBP"}),
}


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """Everything the platform records about an image."""

    width: int
    height: int
    #: Pillow's format name, e.g. ``JPEG``. Kept for diagnostics.
    format: str
    #: Colour mode, e.g. ``RGB``, ``RGBA``, ``CMYK``.
    mode: str

    @property
    def pixels(self) -> int:
        return self.width * self.height


def probe_image_bytes(
    data: bytes,
    *,
    mime_type: str,
    settings: Settings | None = None,
) -> ImageMetadata:
    """Read dimensions from image bytes, rejecting anything unsafe.

    Loading the whole image into memory is acceptable here and only here: the
    byte-size limit for images is small (20 MB by default) and was already
    enforced against the object's real length before this is called. Video
    never takes this path — see :mod:`backend_core.media.probe`.

    Raises :class:`AssetInvalidError` for anything that is not a decodable
    image of the declared type and within the configured pixel limits.
    """
    resolved = settings or get_settings()

    try:
        with Image.open(io.BytesIO(data)) as image:
            # `Image.open` is lazy: it parses the header and stops. Dimensions
            # are available now, before any pixel data is decoded, which is
            # exactly what makes the bomb check cheap enough to be a gate.
            width, height = image.size
            image_format = image.format or "UNKNOWN"
            mode = image.mode
    except UnidentifiedImageError as exc:
        raise AssetInvalidError("The file is not a readable image.") from exc
    except Image.DecompressionBombError as exc:
        raise AssetInvalidError(
            "The image is too large to process.",
            details={"reason": "decompression_bomb"},
        ) from exc
    except OSError as exc:
        # Truncated or corrupt files surface as OSError from the decoder.
        raise AssetInvalidError("The image file is corrupt or incomplete.") from exc

    expected = _EXPECTED_FORMATS.get(mime_type)
    if expected is not None and image_format not in expected:
        raise AssetInvalidError(
            "The image contents do not match the declared file type.",
            details={"declared": mime_type, "detected": image_format},
        )

    if width <= 0 or height <= 0:
        raise AssetInvalidError("The image has no dimensions.")

    largest = max(width, height)
    if largest > resolved.max_upload_image_dimension:
        raise AssetInvalidError(
            f"The image is larger than {resolved.max_upload_image_dimension} pixels "
            "on its longest side.",
            details={"width": width, "height": height},
        )

    if width * height > resolved.max_upload_image_pixels:
        raise AssetInvalidError(
            f"The image exceeds the {resolved.max_upload_image_megapixels} megapixel limit.",
            details={"width": width, "height": height},
        )

    return ImageMetadata(width=width, height=height, format=image_format, mode=mode)
