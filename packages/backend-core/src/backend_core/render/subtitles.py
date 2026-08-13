"""Subtitle cues and SRT output (§31, PHASE 12).

§31 names the three inputs — the script's per-shot voiceover, the TTS duration,
and the timeline — and the MVP output format, SRT.

The timing comes from **measured** TTS durations, never from a guess about
reading speed. That is the whole reason §30's provider contract returns a
duration: a subtitle that appears half a second after the word is spoken is
more distracting than no subtitle at all, and only the synthesiser knows how
long its own audio is.

Long lines are split rather than shrunk. A cue that fills the frame is
unreadable on a phone, and §36's portrait output is where most of this will be
watched.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Characters per cue before it is split. Chinese subtitles run about this wide
#: on a 1080px portrait frame at a legible size; longer lines wrap and cover
#: the product.
MAX_CUE_CHARS: int = 18

#: A cue shorter than this cannot be read even if it is correct.
MIN_CUE_MS: int = 800


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """One line of subtitle, timed against the finished video (§31)."""

    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def build_cues(
    segments: list[tuple[str, int, int]], *, max_chars: int = MAX_CUE_CHARS
) -> list[SubtitleCue]:
    """Turn timed narration into readable cues (§31).

    `segments` is `(text, start_ms, end_ms)` — one per shot, timed by the
    synthesiser. A segment whose text is too long for one cue is split into
    several, sharing the segment's duration **in proportion to their length**
    rather than evenly: a two-character fragment and a twenty-character one do
    not take the same time to say.
    """
    cues: list[SubtitleCue] = []

    for text, start_ms, end_ms in segments:
        cleaned = text.strip()
        if not cleaned:
            continue

        parts = _split(cleaned, max_chars)
        total_chars = sum(len(part) for part in parts) or 1
        cursor = start_ms
        span = max(MIN_CUE_MS, end_ms - start_ms)

        for index, part in enumerate(parts):
            share = int(span * len(part) / total_chars)
            # The last part absorbs the rounding, so the cues end exactly where
            # the narration does rather than a few milliseconds short.
            finish = end_ms if index == len(parts) - 1 else cursor + share
            if finish - cursor < MIN_CUE_MS:
                finish = min(end_ms, cursor + MIN_CUE_MS)
            if finish <= cursor:
                continue
            cues.append(SubtitleCue(start_ms=cursor, end_ms=finish, text=part))
            cursor = finish

    return cues


def _split(text: str, max_chars: int) -> list[str]:
    """Break a line at punctuation where possible, mid-word only if forced.

    Punctuation-first because a cue that breaks mid-clause reads worse than one
    slightly over length — and Chinese has no spaces to fall back on.
    """
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(
            (window.rfind(mark) for mark in ("，", "。", "、", "；", "！", "？", ", ", ". ")),
            default=-1,
        )
        if cut < max_chars // 2:
            # No usable break point in the second half of the window; a hard
            # cut is better than a cue twice the readable width.
            cut = max_chars - 1
        parts.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()

    if remaining:
        parts.append(remaining)
    return [part for part in parts if part]


def to_srt(cues: list[SubtitleCue]) -> str:
    """Render cues as SRT (§31's MVP format).

    SRT rather than ASS because every platform accepts it and the styling §31
    asks for — font, size, position, outline, shadow — is applied at burn-in by
    ffmpeg's `subtitles` filter, not carried in the file.
    """
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start_ms)} --> {_timestamp(cue.end_ms)}\n{cue.text}\n"
        )
    return "\n".join(blocks)


def _timestamp(milliseconds: int) -> str:
    """SRT's `HH:MM:SS,mmm`. The comma is not a typo — SRT requires it."""
    total_seconds, ms = divmod(max(0, milliseconds), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


__all__ = ["MAX_CUE_CHARS", "MIN_CUE_MS", "SubtitleCue", "build_cues", "to_srt"]
