"""ffprobe adapter (taskbook §4.7, §35, P4-T06).

The single place the platform learns what is actually inside a media
container: duration, resolution, frame rate and codecs. Everything downstream —
timeline maths, render presets, QC (§59) — reads these numbers, so a wrong or
guessed value propagates into every generated video.

`ffprobe` is invoked as a subprocess rather than through a Python binding
because the binding ecosystem lags ffmpeg releases badly and the JSON output is
a stable, documented contract. That choice brings its own hazards, which is
what most of this module is about:

* **Argument injection.** A source string beginning with ``-`` would be parsed
  as a flag. The argument vector is built as a list (never a shell string) and
  the source is refused if it could be read as an option.
* **Protocol abuse.** ffmpeg speaks dozens of protocols, several of which read
  local files or spawn network connections from inside a *playlist*. The
  whitelist is narrowed to exactly what the call site needs.
* **Unbounded runtime.** A file whose index sits at the end forces ffprobe to
  seek through the whole thing. Every invocation carries a timeout.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from backend_core.config import Settings, get_settings
from backend_core.observability.logging import get_logger

# `subprocess` above is always invoked with a fixed argv list and shell=False.

logger = get_logger(__name__)

#: Reading a local file must not be able to reach the network, and reading a
#: presigned URL must not be able to reach the filesystem. `tcp`/`tls` are the
#: transports `http`/`https` are built on and are required alongside them.
_LOCAL_PROTOCOLS: Final[str] = "file"
_REMOTE_PROTOCOLS: Final[str] = "http,https,tcp,tls"


class MediaProbeError(RuntimeError):
    """ffprobe could not describe the source.

    Either the file is not media the toolchain understands, or probing failed
    for an operational reason. The service layer translates this into
    ``ASSET_INVALID`` for uploads; a worker re-hosting a provider result treats
    it as a retryable fault instead, which is why the distinction is not baked
    in here.
    """


@dataclass(frozen=True, slots=True)
class MediaStreamInfo:
    """What a container turned out to hold."""

    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container_format: str | None = None
    size_bytes: int | None = None
    bit_rate: int | None = None
    #: Stream-level detail kept for diagnostics and for QC in PHASE 14.
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def duration_seconds(self) -> float | None:
        return None if self.duration_ms is None else self.duration_ms / 1000.0


def probe_media(
    source: str | Path,
    *,
    settings: Settings | None = None,
    timeout_seconds: int | None = None,
) -> MediaStreamInfo:
    """Describe a local file or an HTTP(S) URL.

    Passing a URL is the normal production path: ffprobe reads container
    headers over ranged requests, so a 500 MB upload is inspected without the
    API ever holding the bytes (§116 — large media does not pass through the
    API). A :class:`Path` is accepted for workers that already have the file on
    disk, and for tests.
    """
    resolved = settings or get_settings()
    argv = _build_argv(source, resolved)
    timeout = timeout_seconds or resolved.media_probe_timeout_seconds

    try:
        completed = subprocess.run(  # noqa: S603 - argv is a fixed list; shell=False
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise MediaProbeError(
            f"ffprobe not found at {resolved.ffprobe_path!r}. Install ffmpeg or set FFPROBE_PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(f"ffprobe timed out after {timeout}s") from exc

    if completed.returncode != 0:
        # stderr can echo the source, which for a presigned URL carries a
        # signature. Truncated and logged rather than returned to the caller.
        logger.warning(
            "ffprobe_failed",
            extra={"returncode": completed.returncode, "stderr": completed.stderr[:500]},
        )
        raise MediaProbeError("The file could not be read as media.")

    try:
        payload: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError("ffprobe returned output that could not be parsed.") from exc

    return _parse(payload)


def _build_argv(source: str | Path, settings: Settings) -> list[str]:
    """Assemble the argument vector, refusing a source that reads as a flag."""
    is_path = isinstance(source, Path)
    source_str = str(source)

    if not source_str:
        raise MediaProbeError("No media source given.")
    if source_str.startswith("-"):
        raise MediaProbeError("Invalid media source.")

    if is_path:
        protocols = _LOCAL_PROTOCOLS
    elif source_str.startswith(("http://", "https://")):
        protocols = _REMOTE_PROTOCOLS
    else:
        # Anything else — `concat:`, `pipe:`, a bare relative path from an
        # untrusted caller — is refused rather than guessed at.
        raise MediaProbeError("Unsupported media source scheme.")

    return [
        settings.ffprobe_path,
        "-hide_banner",
        "-v",
        "error",
        "-protocol_whitelist",
        protocols,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        source_str,
    ]


def _parse(payload: dict[str, Any]) -> MediaStreamInfo:
    """Turn ffprobe's JSON into the platform's own shape."""
    container: dict[str, Any] = payload.get("format") or {}
    streams: list[dict[str, Any]] = payload.get("streams") or []

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # Container duration is authoritative; a stream may be shorter than the
    # file (a still cover image inside an MP3, for instance).
    duration_ms = _duration_ms(container.get("duration"))
    if duration_ms is None and video is not None:
        duration_ms = _duration_ms(video.get("duration"))

    return MediaStreamInfo(
        duration_ms=duration_ms,
        width=_positive_int(video.get("width")) if video else None,
        height=_positive_int(video.get("height")) if video else None,
        fps=_frame_rate(video) if video else None,
        video_codec=_text(video.get("codec_name")) if video else None,
        audio_codec=_text(audio.get("codec_name")) if audio else None,
        container_format=_text(container.get("format_name")),
        size_bytes=_positive_int(container.get("size")),
        bit_rate=_positive_int(container.get("bit_rate")),
        raw={"format": container, "streams": streams},
    )


def _duration_ms(value: Any) -> int | None:
    """Seconds-as-string to milliseconds.

    ffprobe reports `N/A` for streams it cannot measure, and the field is
    absent entirely for some containers — both mean "unknown", not zero.
    """
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return round(seconds * 1000)


def _frame_rate(stream: dict[str, Any]) -> float | None:
    """Parse `r_frame_rate`, which is a rational string like ``30000/1001``.

    `avg_frame_rate` is the fallback: `r_frame_rate` is the *base* rate and is
    reported as ``0/0`` for some variable-frame-rate sources.
    """
    for key in ("r_frame_rate", "avg_frame_rate"):
        rate = _rational(stream.get(key))
        if rate is not None:
            return rate
    return None


def _rational(value: Any) -> float | None:
    """``"30000/1001"`` to ``29.97...``; anything unusable to ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return float(rate) if rate > 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
