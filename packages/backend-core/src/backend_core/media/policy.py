"""What may be uploaded, and how it is proven to be what it claims (§12).

Two rules shape this module.

**The whitelist is closed.** §12 names the formats the platform accepts; a type
that is not listed is rejected rather than given a permissive fallback. That is
the difference between an upload endpoint and an arbitrary file host.

**The client's word is never taken.** A browser sends a `Content-Type` it
chose, so the declared type is treated as a *request* to store a certain kind
of file. After the bytes land, the container header is read back and matched
against that claim (:func:`verify_content_signature`). A file named `.jpg`,
declared `image/jpeg`, containing HTML is the classic stored-XSS vector, and it
is caught here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import AssetType
from backend_core.errors import AssetInvalidError, UploadTooLargeError
from backend_core.storage.keys import extension_for_mime_type


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """The rules that apply to one accepted MIME type."""

    mime_type: str
    asset_type: AssetType
    extension: str
    max_bytes: int


@dataclass(frozen=True, slots=True)
class _Signature:
    """A magic-byte pattern: literal bytes expected at fixed offsets.

    Multiple patterns per format because container families legitimately vary —
    an MP3 may start with an ID3 tag or with a raw frame sync.
    """

    #: Each entry is ``(offset, expected_bytes)``; all must match.
    parts: tuple[tuple[int, bytes], ...]

    def matches(self, header: bytes) -> bool:
        return all(
            header[offset : offset + len(expected)] == expected for offset, expected in self.parts
        )


# ISO base media format (MP4, MOV, M4A and friends): a `ftyp` box at offset 4.
# `video/mp4` and `video/quicktime` are deliberately *not* distinguished by
# their brand: a QuickTime-authored file frequently carries an `mp42` brand and
# vice versa, so insisting the brand agree with the declared MIME type would
# reject valid footage while adding no security. What matters is that the bytes
# are an ISO-BMFF container at all, not which of the two labels the browser
# guessed.
_ISO_BMFF: Final[_Signature] = _Signature(parts=((4, b"ftyp"),))

_SIGNATURES: Final[dict[str, tuple[_Signature, ...]]] = {
    "image/jpeg": (_Signature(parts=((0, b"\xff\xd8\xff"),)),),
    "image/png": (_Signature(parts=((0, b"\x89PNG\r\n\x1a\n"),)),),
    # RIFF containers name their payload at offset 8.
    "image/webp": (_Signature(parts=((0, b"RIFF"), (8, b"WEBP"))),),
    "video/mp4": (_ISO_BMFF,),
    "video/quicktime": (_ISO_BMFF,),
}

#: Enough to cover every offset above with room to spare. Kept small on
#: purpose — this is a ranged read against object storage on every completed
#: upload.
_HEADER_BYTES: Final[int] = 64


def header_bytes_needed() -> int:
    """How many leading bytes :func:`verify_content_signature` requires."""
    return _HEADER_BYTES


def _upload_policies(settings: Settings) -> dict[str, UploadPolicy]:
    """Build the whitelist against the configured size limits.

    Only user-uploadable types appear here. `AUDIO`, `SUBTITLE` and `DOCUMENT`
    are valid :class:`AssetType` values, but the platform *produces* them —
    voiceover from TTS (§10.19), subtitles from the script — rather than
    accepting them from a browser. Adding one later is a single entry plus a
    signature; leaving them out until then keeps the attack surface to the
    five formats §12 actually calls for.
    """
    image_max = settings.max_upload_image_bytes
    video_max = settings.max_upload_video_bytes
    return {
        mime: UploadPolicy(
            mime_type=mime,
            asset_type=asset_type,
            extension=extension_for_mime_type(mime),
            max_bytes=image_max if asset_type is AssetType.IMAGE else video_max,
        )
        for mime, asset_type in (
            ("image/jpeg", AssetType.IMAGE),
            ("image/png", AssetType.IMAGE),
            ("image/webp", AssetType.IMAGE),
            ("video/mp4", AssetType.VIDEO),
            ("video/quicktime", AssetType.VIDEO),
        )
    }


def normalise_mime_type(mime_type: str) -> str:
    """Strip parameters and case. ``IMAGE/JPEG; charset=x`` is ``image/jpeg``."""
    return mime_type.split(";")[0].strip().lower()


def supported_upload_mime_types(settings: Settings | None = None) -> tuple[str, ...]:
    """Every MIME type the presign endpoint accepts.

    Surfaced to the client so the file picker and the server agree on one list
    instead of drifting apart.
    """
    return tuple(_upload_policies(settings or get_settings()))


def policy_for_mime_type(mime_type: str, settings: Settings | None = None) -> UploadPolicy:
    """Look up the rules for a declared type, or reject it."""
    resolved = settings or get_settings()
    policy = _upload_policies(resolved).get(normalise_mime_type(mime_type))
    if policy is None:
        raise AssetInvalidError(
            "That file type is not supported.",
            details={
                "mime_type": mime_type,
                "supported": list(supported_upload_mime_types(resolved)),
            },
        )
    return policy


def validate_declared_upload(
    *,
    mime_type: str,
    size_bytes: int,
    filename: str | None = None,
    settings: Settings | None = None,
) -> UploadPolicy:
    """Check what can be checked before a single byte has been transferred.

    Called at presign time. Rejecting an oversized file here costs one request;
    discovering it after a 500 MB transfer costs the user the transfer.

    The declared size is not trusted as truth — it is re-read from storage at
    completion — but a client that *admits* to being over the limit is refused
    immediately.
    """
    resolved = settings or get_settings()
    policy = policy_for_mime_type(mime_type, resolved)

    if size_bytes <= 0:
        raise AssetInvalidError("The file is empty.", details={"size_bytes": size_bytes})

    if size_bytes > policy.max_bytes:
        raise UploadTooLargeError(
            f"That file is larger than the {policy.max_bytes // (1024 * 1024)} MB limit "
            f"for {policy.asset_type.value.lower()} uploads.",
            details={"size_bytes": size_bytes, "max_bytes": policy.max_bytes},
        )

    # The extension is not used to build the object key (§11 — the server names
    # the file), so a mismatch is not a security control. It is still worth
    # refusing, because it almost always means the user picked the wrong file
    # and would otherwise find out only when the preview fails to render.
    if filename and "." in filename:
        declared_extension = filename.rsplit(".", 1)[-1].lower()
        if declared_extension not in _acceptable_extensions(policy):
            raise AssetInvalidError(
                "The file extension does not match the file type.",
                details={"filename": filename, "mime_type": policy.mime_type},
            )

    return policy


def _acceptable_extensions(policy: UploadPolicy) -> frozenset[str]:
    """Extensions a user may plausibly have for this type."""
    aliases: dict[str, frozenset[str]] = {
        "image/jpeg": frozenset({"jpg", "jpeg", "jpe"}),
        "video/quicktime": frozenset({"mov", "qt"}),
    }
    return aliases.get(policy.mime_type, frozenset({policy.extension}))


def verify_content_signature(header: bytes, policy: UploadPolicy) -> None:
    """Confirm the stored bytes really are the declared format.

    Runs after the upload, on a ranged read of the first
    :func:`header_bytes_needed` bytes. This is the check that makes the
    presigned-URL flow safe: the browser wrote straight to storage without the
    API seeing the payload, so this is the *only* point at which the content
    itself is examined.
    """
    signatures = _SIGNATURES.get(policy.mime_type)
    if signatures is None:  # pragma: no cover - unreachable while the two maps agree
        raise AssetInvalidError(
            "That file type cannot be verified.", details={"mime_type": policy.mime_type}
        )

    if not any(signature.matches(header) for signature in signatures):
        raise AssetInvalidError(
            "The file contents do not match the declared file type.",
            details={"mime_type": policy.mime_type},
        )
