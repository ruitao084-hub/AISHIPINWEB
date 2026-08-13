"""The timeline: the render's single source of truth (§33).

§33 states the rule that shapes this module:

    所有自动合成必须先生成 Timeline。
    禁止在 Render Worker 中临时"猜测"镜头顺序。

Every composition builds a timeline first, and the render worker never guesses
shot order. That is why the render worker takes a `Timeline` and nothing else:
given one, the output is determined, and two renders of the same timeline
produce the same video. A worker that read shots and durations itself would
make the output depend on whatever the database happened to say at that moment.

The four tracks are §33's. Keeping them separate is what makes the render's
interactions explicit — narration over music is VOICE against BGM, burned-in
captions are SUBTITLE over VIDEO — where a flat item list would leave them
implicit and therefore negotiable.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_core.domain.enums import ASPECT_DIMENSIONS, RENDER_FPS, TrackType

TIMELINE_VERSION: Final[int] = 1


class Canvas(BaseModel):
    """Output dimensions and frame rate (§33, §36)."""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=16, le=7680)
    height: int = Field(ge=16, le=7680)
    fps: int = Field(default=RENDER_FPS, ge=1, le=120)

    @field_validator("width", "height")
    @classmethod
    def _even(cls, value: int) -> int:
        """H.264 with yuv420p requires even dimensions.

        Caught here rather than in ffmpeg's stderr: an odd width fails the
        encode *after* every asset has been downloaded, which is minutes of
        work and a provider bill already spent.
        """
        if value % 2:
            raise ValueError("Canvas dimensions must be even for yuv420p H.264.")
        return value


class TimelineItem(BaseModel):
    """One clip, cue or audio segment placed on a track."""

    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    #: The media to use. Absent for a subtitle cue, which carries text instead.
    asset_id: str | None = None
    object_key: str | None = None

    #: Where inside the source to start, for a clip longer than its slot.
    source_start_ms: int = Field(default=0, ge=0)

    text: str = ""
    #: 0.0-2.0. §34 lays VOICE over BGM, and the music's gain is what makes the
    #: narration audible rather than competing with it.
    gain: float = Field(default=1.0, ge=0.0, le=2.0)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @field_validator("end_ms")
    @classmethod
    def _ordered(cls, end_ms: int, info: Any) -> int:
        start = info.data.get("start_ms", 0)
        if end_ms <= start:
            raise ValueError("A timeline item must end after it starts.")
        return end_ms


class Track(BaseModel):
    """One of §33's four tracks."""

    model_config = ConfigDict(extra="forbid")

    type: TrackType
    items: list[TimelineItem] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def _video_items_do_not_overlap(
        cls, items: list[TimelineItem], info: Any
    ) -> list[TimelineItem]:
        """Video clips must be sequential; audio may overlap.

        The asymmetry is real. Two clips occupying the same instant is a
        composition nobody specified, while narration over music is the normal
        case. Checked here because the failure otherwise appears as a rendered
        video that silently dropped a shot.
        """
        if info.data.get("type") is not TrackType.VIDEO:
            return items
        ordered = sorted(items, key=lambda item: item.start_ms)
        for previous, following in pairwise(ordered):
            if following.start_ms < previous.end_ms:
                raise ValueError(
                    f"Video clips overlap: one ends at {previous.end_ms}ms and the "
                    f"next starts at {following.start_ms}ms."
                )
        return items

    @property
    def duration_ms(self) -> int:
        return max((item.end_ms for item in self.items), default=0)


class Timeline(BaseModel):
    """A complete, renderable composition (§33)."""

    model_config = ConfigDict(extra="forbid")

    version: int = TIMELINE_VERSION
    canvas: Canvas
    tracks: list[Track]

    @field_validator("tracks")
    @classmethod
    def _one_track_per_type(cls, tracks: list[Track]) -> list[Track]:
        kinds = [track.type for track in tracks]
        if len(set(kinds)) != len(kinds):
            raise ValueError("A timeline has at most one track of each type.")
        return tracks

    def track(self, kind: TrackType) -> Track | None:
        return next((track for track in self.tracks if track.type is kind), None)

    @property
    def duration_ms(self) -> int:
        """The finished video's length.

        Driven by the VIDEO track alone. Narration overrunning the last clip
        would otherwise extend the video past its final frame and leave a black
        tail; the mixer trims it instead.
        """
        video = self.track(TrackType.VIDEO)
        return video.duration_ms if video else 0


def build_timeline(
    *,
    aspect_ratio: str,
    clips: list[tuple[str, str, int]],
    voice_segments: list[tuple[str, str, int, int]] | None = None,
    subtitle_cues: list[tuple[str, int, int]] | None = None,
    bgm: tuple[str, str] | None = None,
    bgm_gain: float = 0.18,
    fps: int = RENDER_FPS,
) -> Timeline:
    """Assemble a timeline from a storyboard's finished pieces (§33).

    `clips` is `(asset_id, object_key, duration_ms)` **in shot order**, taken
    from the caller rather than re-derived — §33 forbids the render worker
    guessing shot order, and the same discipline applies to whatever builds the
    timeline it will be handed.

    BGM sits under the whole video at a low default gain. 18% is quiet enough
    that narration reads over it without ducking, which the MVP renderer does
    not do.
    """
    width, height = ASPECT_DIMENSIONS.get(aspect_ratio, ASPECT_DIMENSIONS["9:16"])

    cursor = 0
    video_items: list[TimelineItem] = []
    for asset_id, object_key, duration_ms in clips:
        video_items.append(
            TimelineItem(
                start_ms=cursor,
                end_ms=cursor + duration_ms,
                asset_id=asset_id,
                object_key=object_key,
            )
        )
        cursor += duration_ms

    tracks = [Track(type=TrackType.VIDEO, items=video_items)]

    if voice_segments:
        tracks.append(
            Track(
                type=TrackType.VOICE,
                items=[
                    TimelineItem(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        asset_id=asset_id,
                        object_key=object_key,
                    )
                    for asset_id, object_key, start_ms, end_ms in voice_segments
                ],
            )
        )

    if bgm and cursor > 0:
        bgm_asset_id, bgm_key = bgm
        tracks.append(
            Track(
                type=TrackType.BGM,
                items=[
                    TimelineItem(
                        start_ms=0,
                        end_ms=cursor,
                        asset_id=bgm_asset_id,
                        object_key=bgm_key,
                        gain=bgm_gain,
                    )
                ],
            )
        )

    if subtitle_cues:
        tracks.append(
            Track(
                type=TrackType.SUBTITLE,
                items=[
                    TimelineItem(start_ms=start_ms, end_ms=end_ms, text=text)
                    for text, start_ms, end_ms in subtitle_cues
                ],
            )
        )

    return Timeline(canvas=Canvas(width=width, height=height, fps=fps), tracks=tracks)


#: Sidecar keys stored alongside a serialised timeline.
#:
#: `Timeline` forbids unknown fields, which is what stops a stray key changing
#: a render's meaning — so bookkeeping that is *about* a timeline rather than
#: part of one cannot live inside the document. It rides beside it and is
#: stripped on the way back in. `store_timeline` / `load_timeline` are the only
#: sanctioned pair; validating `render.timeline_json` directly is a bug waiting
#: for the first sidecar key.
EDITED_KEY: Final[str] = "_edited"

_SIDECAR_KEYS: Final[frozenset[str]] = frozenset({EDITED_KEY})


def store_timeline(timeline: Timeline, *, edited: bool = False) -> dict[str, Any]:
    """Serialise a timeline with its sidecar metadata."""
    payload = timeline.model_dump(mode="json")
    payload[EDITED_KEY] = edited
    return payload


def load_timeline(payload: dict[str, Any]) -> tuple[Timeline, bool]:
    """Parse a stored timeline, returning it and whether it was hand-edited.

    Tolerates a payload written before the sidecar existed: a plain dump has no
    `_edited` key and reads as unedited, which is what it is.
    """
    body = {key: value for key, value in payload.items() if key not in _SIDECAR_KEYS}
    return Timeline.model_validate(body), bool(payload.get(EDITED_KEY, False))


__all__ = [
    "EDITED_KEY",
    "TIMELINE_VERSION",
    "Canvas",
    "Timeline",
    "TimelineItem",
    "Track",
    "build_timeline",
    "load_timeline",
    "store_timeline",
]
