"""Technical quality checks (§37.1, PHASE 14).

§37 splits QC in two, and the split is the point. **Technical QC is decidable**
— a file either decodes or it does not, its resolution either matches the
canvas or it does not. **Visual QC is a model's opinion** about whether the
product in the frame is the customer's product, and §37.2 exists because every
technical check passes happily on a beautifully encoded video of the wrong
thing.

This module is the decidable half. It returns findings rather than a verdict:
a clip two frames short is worth telling someone and not worth blocking a
delivery over, and collapsing `WARNING` into `FAILED` would force every
borderline result to be either fatal or ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import QCStatus
from backend_core.media.probe import MediaStreamInfo, probe_media

#: How far the rendered length may sit from the timeline's before it matters.
#: Two frames at 30fps — below that it is encoder rounding, above it something
#: dropped a clip.
_DURATION_TOLERANCE_MS: int = 70

#: Below this, an "audio track" is silence with a header. Common when a TTS
#: step failed quietly and the muxer inserted `anullsrc`.
_MIN_AUDIO_PEAK_DBFS: float = -60.0


@dataclass(frozen=True, slots=True)
class QCFinding:
    """One thing the checker noticed."""

    check: str
    status: QCStatus
    detail: str

    @property
    def blocking(self) -> bool:
        return self.status is QCStatus.FAILED


@dataclass(frozen=True, slots=True)
class QCReport:
    """Everything the technical checks found (§37.1)."""

    findings: list[QCFinding]
    probed: MediaStreamInfo | None = None

    @property
    def status(self) -> QCStatus:
        """The worst finding. One failure fails the report."""
        if any(finding.status is QCStatus.FAILED for finding in self.findings):
            return QCStatus.FAILED
        if any(finding.status is QCStatus.WARNING for finding in self.findings):
            return QCStatus.WARNING
        return QCStatus.PASSED

    @property
    def blocking_findings(self) -> list[QCFinding]:
        return [finding for finding in self.findings if finding.blocking]


def check_rendered_video(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_duration_ms: int,
    expect_audio: bool = True,
    settings: Settings | None = None,
) -> QCReport:
    """Run §37.1's checks against a rendered file.

    Order matters: existence and decodability first, because every later check
    is meaningless if the file cannot be read, and reporting six failures for
    one missing file helps nobody.
    """
    resolved = settings or get_settings()
    findings: list[QCFinding] = []

    if not path.is_file():
        return QCReport(
            findings=[
                QCFinding("file_exists", QCStatus.FAILED, "The rendered file does not exist.")
            ]
        )

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        return QCReport(
            findings=[QCFinding("file_size", QCStatus.FAILED, "The rendered file is empty.")]
        )

    try:
        probed = probe_media(path, settings=resolved)
    except Exception as exc:
        return QCReport(
            findings=[
                QCFinding("decodable", QCStatus.FAILED, f"The file could not be decoded: {exc}")
            ]
        )

    findings.append(QCFinding("file_exists", QCStatus.PASSED, f"{size_bytes} bytes"))
    findings.append(
        QCFinding("decodable", QCStatus.PASSED, probed.container_format or "unknown container")
    )

    # -- resolution ---------------------------------------------------------
    if probed.width != expected_width or probed.height != expected_height:
        findings.append(
            QCFinding(
                "resolution",
                QCStatus.FAILED,
                f"Expected {expected_width}x{expected_height}, got {probed.width}x{probed.height}.",
            )
        )
    else:
        findings.append(QCFinding("resolution", QCStatus.PASSED, f"{probed.width}x{probed.height}"))

    # -- duration -----------------------------------------------------------
    if probed.duration_ms is None:
        findings.append(QCFinding("duration", QCStatus.FAILED, "The file reports no duration."))
    else:
        drift = abs(probed.duration_ms - expected_duration_ms)
        if drift > _DURATION_TOLERANCE_MS:
            # A warning rather than a failure: the video is watchable and a
            # person can decide. A dropped clip shows up as a large drift, and
            # the number in the message is what makes that visible.
            findings.append(
                QCFinding(
                    "duration",
                    QCStatus.WARNING,
                    f"Expected about {expected_duration_ms}ms, got {probed.duration_ms}ms "
                    f"({drift}ms off).",
                )
            )
        else:
            findings.append(QCFinding("duration", QCStatus.PASSED, f"{probed.duration_ms}ms"))

    # -- streams ------------------------------------------------------------
    if not probed.has_video:
        findings.append(QCFinding("video_stream", QCStatus.FAILED, "There is no video stream."))
    else:
        findings.append(QCFinding("video_stream", QCStatus.PASSED, probed.video_codec or ""))

    if expect_audio and not probed.has_audio:
        findings.append(
            QCFinding("audio_stream", QCStatus.FAILED, "Narration was expected but is missing.")
        )
    elif probed.has_audio:
        findings.append(QCFinding("audio_stream", QCStatus.PASSED, probed.audio_codec or ""))

    # -- frame rate ---------------------------------------------------------
    if probed.fps is not None and probed.fps < 20:
        findings.append(
            QCFinding(
                "fps",
                QCStatus.WARNING,
                f"{probed.fps:g} fps will look choppy; 24-30 is expected.",
            )
        )
    elif probed.fps is not None:
        findings.append(QCFinding("fps", QCStatus.PASSED, f"{probed.fps:g} fps"))

    return QCReport(findings=findings, probed=probed)


def check_black_frames(path: Path, *, settings: Settings | None = None) -> QCFinding:
    """Look for a fully black stretch (§37.1).

    A black run usually means a generation failed and the renderer padded, or a
    clip was shorter than its slot. Reported as a warning: a deliberate fade to
    black at the end is legitimate and common, so failing on this would reject
    correct videos.
    """
    import subprocess

    resolved = settings or get_settings()
    argv = [
        resolved.ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-i",
        str(path),
        "-vf",
        "blackdetect=d=0.5:pix_th=0.10",
        "-f",
        "null",
        "-",
    ]

    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, shell=False (§35)
            argv,
            capture_output=True,
            text=True,
            timeout=resolved.media_probe_timeout_seconds,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return QCFinding("black_frames", QCStatus.WARNING, f"Could not check: {exc}")

    hits = [line for line in completed.stderr.splitlines() if "black_start" in line]
    if hits:
        return QCFinding(
            "black_frames",
            QCStatus.WARNING,
            f"{len(hits)} black stretch(es) found: {hits[0].strip()}",
        )
    return QCFinding("black_frames", QCStatus.PASSED, "No long black stretches.")


__all__ = ["QCFinding", "QCReport", "check_black_frames", "check_rendered_video"]
