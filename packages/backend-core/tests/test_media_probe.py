"""ffprobe adapter: argument safety and JSON parsing (§35, P4-T06).

The tests that run ffprobe itself live in ``tests/integration`` — this file
must stay runnable with no media toolchain installed, so everything here works
on canned payloads and on the argument vector rather than on the binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend_core.config import Settings, get_settings
from backend_core.media.probe import MediaProbeError, _build_argv, _parse


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def ffprobe_payload(**overrides: Any) -> dict[str, Any]:
    """A realistic ffprobe response for a 2-second 1080p clip."""
    payload: dict[str, Any] = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "2.002000",
            "size": "1048576",
            "bit_rate": "4190000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "avg_frame_rate": "30000/1001",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    payload.update(overrides)
    return payload


class TestArgumentSafety:
    @pytest.mark.parametrize(
        "source",
        [
            "-i",
            "-f",
            "--help",
        ],
    )
    def test_refuses_a_source_that_reads_as_a_flag(self, source: str, settings: Settings) -> None:
        with pytest.raises(MediaProbeError):
            _build_argv(source, settings)

    @pytest.mark.parametrize(
        "source",
        [
            "concat:/etc/passwd",
            "file:///etc/passwd",
            "pipe:0",
            "ftp://example.com/x.mp4",
            "/etc/passwd",
            "",
        ],
    )
    def test_refuses_any_scheme_that_was_not_asked_for(
        self, source: str, settings: Settings
    ) -> None:
        with pytest.raises(MediaProbeError):
            _build_argv(source, settings)

    def test_local_paths_get_the_file_only_whitelist(self, settings: Settings) -> None:
        """Probing a file on disk must not be able to reach the network."""
        argv = _build_argv(Path("/var/media/clip.mp4"), settings)
        assert argv[argv.index("-protocol_whitelist") + 1] == "file"

    def test_urls_get_the_network_only_whitelist(self, settings: Settings) -> None:
        """Probing a presigned URL must not be able to read the filesystem."""
        argv = _build_argv("https://storage.example.com/x.mp4?sig=abc", settings)
        assert argv[argv.index("-protocol_whitelist") + 1] == "http,https,tcp,tls"

    def test_source_is_passed_behind_an_explicit_input_flag(self, settings: Settings) -> None:
        argv = _build_argv("https://example.com/x.mp4", settings)
        assert argv[-2:] == ["-i", "https://example.com/x.mp4"]


class TestParsing:
    def test_reads_the_fields_the_platform_stores(self) -> None:
        info = _parse(ffprobe_payload())
        assert info.duration_ms == 2002
        assert (info.width, info.height) == (1920, 1080)
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.size_bytes == 1048576
        assert info.has_video and info.has_audio

    def test_ntsc_frame_rate_keeps_its_precision(self) -> None:
        """29.97 is 30000/1001. Rounding it drifts over a long timeline."""
        info = _parse(ffprobe_payload())
        assert info.fps is not None
        assert info.fps == pytest.approx(30000 / 1001)
        assert info.fps != 29.97

    def test_falls_back_to_average_rate_when_the_base_rate_is_unusable(self) -> None:
        payload = ffprobe_payload()
        payload["streams"][0]["r_frame_rate"] = "0/0"
        payload["streams"][0]["avg_frame_rate"] = "25/1"
        assert _parse(payload).fps == 25.0

    def test_missing_frame_rate_is_none_not_zero(self) -> None:
        payload = ffprobe_payload()
        payload["streams"][0]["r_frame_rate"] = "0/0"
        payload["streams"][0]["avg_frame_rate"] = "0/0"
        assert _parse(payload).fps is None

    def test_audio_only_file_reports_no_video(self) -> None:
        payload = ffprobe_payload()
        payload["streams"] = [{"codec_type": "audio", "codec_name": "mp3"}]
        info = _parse(payload)
        assert not info.has_video
        assert info.has_audio
        assert info.width is None

    @pytest.mark.parametrize("duration", ["N/A", None, "", "not-a-number"])
    def test_unmeasurable_duration_is_none_not_zero(self, duration: Any) -> None:
        """`0` would silently pass a "has a duration" check that should fail."""
        payload = ffprobe_payload()
        payload["format"]["duration"] = duration
        payload["streams"][0].pop("duration", None)
        assert _parse(payload).duration_ms is None

    def test_falls_back_to_the_video_stream_duration(self) -> None:
        payload = ffprobe_payload()
        del payload["format"]["duration"]
        payload["streams"][0]["duration"] = "5.5"
        assert _parse(payload).duration_ms == 5500

    def test_empty_response_yields_an_empty_description(self) -> None:
        info = _parse({})
        assert info.duration_ms is None
        assert not info.has_video
        assert not info.has_audio

    def test_duration_seconds_is_derived_from_milliseconds(self) -> None:
        info = _parse(ffprobe_payload())
        assert info.duration_seconds == pytest.approx(2.002)
