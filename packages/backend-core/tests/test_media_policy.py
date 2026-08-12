"""Upload policy and content-signature checks (§12, P4-T04)."""

from __future__ import annotations

import pytest

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import AssetType
from backend_core.errors import AssetInvalidError, UploadTooLargeError
from backend_core.media.policy import (
    header_bytes_needed,
    normalise_mime_type,
    policy_for_mime_type,
    supported_upload_mime_types,
    validate_declared_upload,
    verify_content_signature,
)

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP_HEADER = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 40
MP4_HEADER = b"\x00\x00\x00\x20" + b"ftyp" + b"isom" + b"\x00" * 40


@pytest.fixture
def settings() -> Settings:
    return get_settings()


class TestWhitelist:
    def test_accepts_exactly_the_documented_types(self, settings: Settings) -> None:
        assert set(supported_upload_mime_types(settings)) == {
            "image/jpeg",
            "image/png",
            "image/webp",
            "video/mp4",
            "video/quicktime",
        }

    @pytest.mark.parametrize(
        ("mime_type", "expected"),
        [
            ("image/jpeg", AssetType.IMAGE),
            ("image/png", AssetType.IMAGE),
            ("image/webp", AssetType.IMAGE),
            ("video/mp4", AssetType.VIDEO),
            ("video/quicktime", AssetType.VIDEO),
        ],
    )
    def test_maps_type_to_asset_kind(
        self, mime_type: str, expected: AssetType, settings: Settings
    ) -> None:
        assert policy_for_mime_type(mime_type, settings).asset_type is expected

    @pytest.mark.parametrize(
        "mime_type",
        [
            "image/svg+xml",  # scriptable — the reason the list is closed
            "text/html",
            "application/pdf",
            "application/octet-stream",
            "image/gif",
            "",
        ],
    )
    def test_rejects_everything_else(self, mime_type: str, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            policy_for_mime_type(mime_type, settings)

    def test_normalises_case_and_parameters(self, settings: Settings) -> None:
        assert normalise_mime_type("IMAGE/JPEG; charset=binary") == "image/jpeg"
        policy = policy_for_mime_type("IMAGE/JPEG; charset=binary", settings)
        assert policy.mime_type == "image/jpeg"

    def test_extension_comes_from_the_type_not_the_filename(self, settings: Settings) -> None:
        assert policy_for_mime_type("image/jpeg", settings).extension == "jpg"
        assert policy_for_mime_type("video/quicktime", settings).extension == "mov"


class TestDeclaredUpload:
    def test_accepts_a_plausible_upload(self, settings: Settings) -> None:
        policy = validate_declared_upload(
            mime_type="image/png", size_bytes=1024, filename="shot.png", settings=settings
        )
        assert policy.asset_type is AssetType.IMAGE

    def test_rejects_an_empty_file(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            validate_declared_upload(mime_type="image/png", size_bytes=0, settings=settings)

    def test_rejects_a_file_over_the_image_limit(self, settings: Settings) -> None:
        with pytest.raises(UploadTooLargeError) as excinfo:
            validate_declared_upload(
                mime_type="image/png",
                size_bytes=settings.max_upload_image_bytes + 1,
                settings=settings,
            )
        assert excinfo.value.http_status == 413

    def test_video_limit_is_larger_than_the_image_limit(self, settings: Settings) -> None:
        """A size that is too big for an image is fine for a video."""
        size = settings.max_upload_image_bytes + 1
        assert size <= settings.max_upload_video_bytes
        validate_declared_upload(mime_type="video/mp4", size_bytes=size, settings=settings)

    @pytest.mark.parametrize("filename", ["photo.jpg", "photo.jpeg", "PHOTO.JPG"])
    def test_accepts_every_spelling_of_a_jpeg_extension(
        self, filename: str, settings: Settings
    ) -> None:
        validate_declared_upload(
            mime_type="image/jpeg", size_bytes=1024, filename=filename, settings=settings
        )

    def test_rejects_an_extension_that_contradicts_the_type(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            validate_declared_upload(
                mime_type="image/png", size_bytes=1024, filename="photo.jpg", settings=settings
            )

    def test_accepts_a_filename_with_no_extension(self, settings: Settings) -> None:
        """The key is server-generated, so a bare name is not a problem (§11)."""
        validate_declared_upload(
            mime_type="image/png", size_bytes=1024, filename="screenshot", settings=settings
        )


class TestContentSignature:
    @pytest.mark.parametrize(
        ("mime_type", "header"),
        [
            ("image/png", PNG_HEADER),
            ("image/jpeg", JPEG_HEADER),
            ("image/webp", WEBP_HEADER),
            ("video/mp4", MP4_HEADER),
            ("video/quicktime", MP4_HEADER),
        ],
    )
    def test_accepts_a_matching_header(
        self, mime_type: str, header: bytes, settings: Settings
    ) -> None:
        verify_content_signature(header, policy_for_mime_type(mime_type, settings))

    def test_rejects_html_declared_as_an_image(self, settings: Settings) -> None:
        """The stored-XSS case: a `.jpg` that a browser would render as markup."""
        payload = b"<html><script>alert(1)</script></html>" + b"\x00" * 40
        with pytest.raises(AssetInvalidError):
            verify_content_signature(payload, policy_for_mime_type("image/jpeg", settings))

    def test_rejects_a_png_declared_as_a_jpeg(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            verify_content_signature(PNG_HEADER, policy_for_mime_type("image/jpeg", settings))

    def test_rejects_an_svg_declared_as_a_png(self, settings: Settings) -> None:
        payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>' + b"\x00" * 40
        with pytest.raises(AssetInvalidError):
            verify_content_signature(payload, policy_for_mime_type("image/png", settings))

    def test_rejects_a_truncated_header(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            verify_content_signature(b"\x89PN", policy_for_mime_type("image/png", settings))

    def test_rejects_an_empty_object(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            verify_content_signature(b"", policy_for_mime_type("image/png", settings))

    def test_riff_container_must_name_webp(self, settings: Settings) -> None:
        """A WAV is also a RIFF file; only the payload tag separates them."""
        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 40
        with pytest.raises(AssetInvalidError):
            verify_content_signature(wav, policy_for_mime_type("image/webp", settings))

    def test_header_budget_covers_every_signature_offset(self) -> None:
        """The ranged read must be long enough for the furthest offset checked."""
        assert header_bytes_needed() >= 12
