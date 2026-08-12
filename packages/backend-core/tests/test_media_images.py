"""Image inspection and its safety limits (§12, P4-T05)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from backend_core.config import Settings, get_settings
from backend_core.errors import AssetInvalidError
from backend_core.media.images import probe_image_bytes


def encode(width: int, height: int, image_format: str, mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color="red").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


class TestDimensions:
    @pytest.mark.parametrize(
        ("image_format", "mime_type"),
        [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
    )
    def test_reads_dimensions_for_every_accepted_format(
        self, image_format: str, mime_type: str, settings: Settings
    ) -> None:
        metadata = probe_image_bytes(
            encode(120, 80, image_format), mime_type=mime_type, settings=settings
        )
        assert (metadata.width, metadata.height) == (120, 80)
        assert metadata.pixels == 9600
        assert metadata.format == image_format

    def test_reports_the_colour_mode(self, settings: Settings) -> None:
        metadata = probe_image_bytes(
            encode(10, 10, "PNG", mode="RGBA"), mime_type="image/png", settings=settings
        )
        assert metadata.mode == "RGBA"


class TestRejection:
    def test_rejects_bytes_that_are_not_an_image(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            probe_image_bytes(b"not an image at all", mime_type="image/png", settings=settings)

    def test_rejects_an_empty_body(self, settings: Settings) -> None:
        with pytest.raises(AssetInvalidError):
            probe_image_bytes(b"", mime_type="image/png", settings=settings)

    def test_rejects_a_png_declared_as_a_jpeg(self, settings: Settings) -> None:
        """Format confusion, caught a second time after the signature check."""
        with pytest.raises(AssetInvalidError) as excinfo:
            probe_image_bytes(encode(10, 10, "PNG"), mime_type="image/jpeg", settings=settings)
        assert excinfo.value.details["detected"] == "PNG"

    def test_rejects_a_truncated_file(self, settings: Settings) -> None:
        data = encode(64, 64, "PNG")
        with pytest.raises(AssetInvalidError):
            probe_image_bytes(data[:8], mime_type="image/png", settings=settings)


class TestLimits:
    def test_rejects_an_image_over_the_pixel_budget(self, settings: Settings) -> None:
        """The decompression-bomb guard, checked from the header alone.

        A 400x400 PNG of one flat colour is under a kilobyte; against a
        1-megapixel-equivalent budget it is still refused, which is the point —
        the byte size says nothing about the decoded size.
        """
        tight = settings.model_copy(update={"max_upload_image_megapixels": 1})
        data = encode(1200, 1200, "PNG")
        assert len(data) < 100_000
        with pytest.raises(AssetInvalidError) as excinfo:
            probe_image_bytes(data, mime_type="image/png", settings=tight)
        assert excinfo.value.details["width"] == 1200

    def test_rejects_an_image_over_the_dimension_cap(self, settings: Settings) -> None:
        tight = settings.model_copy(update={"max_upload_image_dimension": 100})
        with pytest.raises(AssetInvalidError):
            probe_image_bytes(encode(200, 10, "PNG"), mime_type="image/png", settings=tight)

    def test_accepts_an_image_exactly_at_the_dimension_cap(self, settings: Settings) -> None:
        exact = settings.model_copy(update={"max_upload_image_dimension": 200})
        metadata = probe_image_bytes(encode(200, 10, "PNG"), mime_type="image/png", settings=exact)
        assert metadata.width == 200
