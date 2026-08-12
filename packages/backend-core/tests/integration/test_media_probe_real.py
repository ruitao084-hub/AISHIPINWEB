"""ffprobe against a real file (P4-T06).

Marked `integration` because it needs the media toolchain installed, which the
serviceless unit job deliberately does not have. It is *not* skipped when
ffprobe is missing: a probe test that quietly disappears is worse than no test,
because the phase that depends on it (rendering, §35) would look covered.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend_core.config import get_settings
from backend_core.media.probe import MediaProbeError, probe_media

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "media" / "tiny.mp4"


class TestRealProbe:
    def test_ffprobe_is_installed(self) -> None:
        """Fails loudly rather than skipping — §35 depends on this binary."""
        assert shutil.which(get_settings().ffprobe_path) is not None

    def test_describes_a_real_container(self) -> None:
        info = probe_media(FIXTURE)

        assert info.duration_ms == 1000
        assert (info.width, info.height) == (64, 64)
        assert info.fps == 24.0
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.container_format is not None and "mp4" in info.container_format
        assert info.size_bytes == FIXTURE.stat().st_size

    def test_refuses_a_file_that_is_not_media(self, tmp_path: Path) -> None:
        not_media = tmp_path / "notes.txt"
        not_media.write_text("this is not a video")

        with pytest.raises(MediaProbeError):
            probe_media(not_media)

    def test_refuses_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(MediaProbeError):
            probe_media(tmp_path / "absent.mp4")

    def test_a_timeout_is_reported_not_hung(self) -> None:
        """The timeout is real, not a parameter nothing reads."""
        with pytest.raises(MediaProbeError):
            probe_media("https://10.255.255.1/never-answers.mp4", timeout_seconds=1)
