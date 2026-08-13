"""Editing the cut without regenerating anything (§97, PHASE 20).

§97's acceptance is one sentence:

    无需重新生成 AI Shot 即可调整剪辑。

That is the whole design constraint, and it is satisfied by a fact already true
since PHASE 13: the render worker consumes a `Timeline` and nothing else (§33).
Reordering, trimming, changing a gain or a subtitle colour are edits to that
document. None of them touch a shot, so none of them costs a generation.

So this module does not add an editing engine. It adds:

1. a **draft timeline** per project — the same structure `create_render` builds,
   stored so it survives a page reload and can be edited between renders;
2. **edits as operations** rather than a whole-document PUT;
3. a **re-render** that starts from the edited draft instead of rebuilding from
   shots.

**Why operations rather than accepting a Timeline from the client.** A client
that sends a whole timeline can send one referencing an object key from another
workspace, or with a clip duration nobody generated. Validating an arbitrary
submitted document against "is every key one this project owns" is the same
work as applying operations, with a larger surface to get wrong. Operations
name what may change; everything else is not expressible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import JobType, LogoPosition, RenderStatus, TrackType
from backend_core.domain.models import GenerationJob, MediaAsset, Render
from backend_core.errors import NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.render.timeline import (
    Timeline,
    TimelineItem,
    Track,
    load_timeline,
    store_timeline,
)
from backend_core.repositories.renders import RenderRepository
from backend_core.services.jobs import JobService
from backend_core.services.post_production import PostProductionService

logger = get_logger(__name__)

#: A clip may be trimmed to this fraction of its generated length at least.
#: Below about a second nothing reads as a shot — it reads as a glitch — and a
#: timeline of 200ms clips is a mistake rather than an edit.
MIN_CLIP_MS: Final[int] = 800

#: Gain ceiling. Above 2.0 an mp3 mixdown clips audibly, and the honest fix for
#: quiet narration is re-synthesis, not amplification.
MAX_GAIN: Final[float] = 2.0


# --- edit operations (P20-T02 through T07) ----------------------------------


class ReorderShots(BaseModel):
    """P20-T02. The new order, as clip indices into the VIDEO track."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["reorder"] = "reorder"
    order: list[int] = Field(min_length=1, max_length=64)


class TrimClip(BaseModel):
    """P20-T03. Shorten or lengthen one clip's slot on the timeline.

    `source_start_ms` moves the in-point *within* the generated clip; changing
    it is how a trim keeps the interesting part rather than always the first
    seconds.
    """

    model_config = ConfigDict(extra="forbid")
    op: Literal["trim"] = "trim"
    index: int = Field(ge=0)
    duration_ms: int = Field(ge=MIN_CLIP_MS)
    source_start_ms: int = Field(default=0, ge=0)


class SetTrackGain(BaseModel):
    """P20-T05 and P20-T06 — one operation, because they are one thing."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["gain"] = "gain"
    track: Literal["VOICE", "BGM"]
    gain: float = Field(ge=0.0, le=MAX_GAIN)


class SetSubtitleStyle(BaseModel):
    """P20-T04. Style is metadata on the SUBTITLE track, not free-form text.

    A closed set of fields rather than a style string, because the value ends
    up inside ffmpeg's `force_style` argument (§35) and a string a user typed
    there is an injection into the filter grammar.
    """

    model_config = ConfigDict(extra="forbid")
    op: Literal["subtitle_style"] = "subtitle_style"
    font_size: int | None = Field(default=None, ge=8, le=72)
    #: `#RRGGBB`. The one colour format with no filter metacharacters in it.
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    margin_v: int | None = Field(default=None, ge=0, le=400)
    enabled: bool | None = None


class SetLogo(BaseModel):
    """P20-T07. §141 says a missing logo must never block a render."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["logo"] = "logo"
    asset_id: uuid.UUID | None = None
    position: LogoPosition = LogoPosition.BOTTOM_RIGHT
    opacity: float = Field(default=0.85, ge=0.0, le=1.0)
    scale: float = Field(default=0.12, ge=0.02, le=0.5)


EditOperation = ReorderShots | TrimClip | SetTrackGain | SetSubtitleStyle | SetLogo


@dataclass(frozen=True, slots=True)
class DraftTimeline:
    """A project's editable cut, and whether it has diverged from its shots."""

    project_id: uuid.UUID
    timeline: Timeline
    #: True when edits have been applied since the draft was built from shots.
    edited: bool
    duration_ms: int


class TimelineEditor:
    """§97's editor: change the cut, never the shots."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._renders = RenderRepository(session)
        self._production = PostProductionService(session, settings=self._settings)

    # -- the draft (P20-T01) ------------------------------------------------

    async def draft(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> DraftTimeline:
        """The project's editable timeline.

        Returns the most recent render's timeline when one exists — that is the
        document the user has been editing — and builds a fresh one from the
        shots otherwise. Rebuilding unconditionally would silently discard
        every edit the moment somebody reopened the page.
        """
        latest = await self._latest_draft(workspace_id, project_id)
        if latest is not None:
            timeline, edited = load_timeline(latest.timeline_json)
            return DraftTimeline(
                project_id=project_id,
                timeline=timeline,
                edited=edited,
                duration_ms=timeline.duration_ms,
            )

        # Composed, not rendered: opening the editor must not start an encode.
        # `compose_timeline` exists for exactly this — it assembles without
        # storing a job.
        timeline = await self._production.compose_timeline(
            workspace_id=workspace_id, project_id=project_id
        )
        version = await self._renders.next_version(workspace_id, project_id)
        render = Render(
            workspace_id=workspace_id,
            project_id=project_id,
            version=version,
            status=RenderStatus.PENDING,
            timeline_json=timeline.model_dump(mode="json"),
            width=timeline.canvas.width,
            height=timeline.canvas.height,
            duration_ms=timeline.duration_ms,
        )
        self._session.add(render)
        await self._session.flush()

        return DraftTimeline(
            project_id=project_id,
            timeline=timeline,
            edited=False,
            duration_ms=timeline.duration_ms,
        )

    async def _latest_draft(self, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Render | None:
        """The newest render row, finished or not — that is the working copy."""
        result = await self._session.execute(
            select(Render)
            .where(Render.workspace_id == workspace_id, Render.project_id == project_id)
            .order_by(Render.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # -- edits (P20-T02 through T07) ----------------------------------------

    async def apply(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        operations: list[EditOperation],
    ) -> DraftTimeline:
        """Apply edits to the draft and store the result.

        Every operation is applied to an in-memory copy and the whole set is
        stored once. Partial application would leave a timeline that is half
        one edit and half another, which is worse than refusing the batch.
        """
        draft = await self.draft(workspace_id=workspace_id, project_id=project_id)
        timeline = draft.timeline

        for operation in operations:
            if isinstance(operation, SetLogo) and operation.asset_id is not None:
                # Resolved here rather than at render time, and scoped to this
                # workspace (§11): a timeline naming another tenant's object
                # would otherwise fail deep inside a worker, where the error is
                # a stack trace rather than a message.
                asset = await self.asset_for_logo(
                    workspace_id=workspace_id, asset_id=operation.asset_id
                )
                timeline = _set_logo(timeline, operation, object_key=asset.object_key)
                continue
            timeline = _apply_one(timeline, operation)

        # Re-laying the video track closes the gaps a trim or a reorder leaves.
        # Without it, trimming clip two by a second produces a second of black
        # rather than a shorter video.
        timeline = _resequence(timeline)

        render = await self._latest_draft(workspace_id, project_id)
        if render is None:  # pragma: no cover — `draft` just created one
            raise NotFoundError("This project has no timeline to edit.")

        render.timeline_json = store_timeline(timeline, edited=True)
        render.duration_ms = timeline.duration_ms
        render.width = timeline.canvas.width
        render.height = timeline.canvas.height
        await self._session.flush()

        logger.info(
            "timeline_edited",
            extra={
                "project_id": str(project_id),
                "operations": [operation.op for operation in operations],
                "duration_ms": timeline.duration_ms,
            },
        )
        return DraftTimeline(
            project_id=project_id,
            timeline=timeline,
            edited=True,
            duration_ms=timeline.duration_ms,
        )

    # -- re-render (P20-T08) ------------------------------------------------

    async def rerender(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> tuple[Render, GenerationJob, bool]:
        """Encode the edited timeline (§97, P20-T08).

        The point of the phase: this queues a render job and *no* generation
        job. The clips already exist; only the composition changed.

        A new `Render` row rather than re-running the draft's: the previous
        output stays downloadable while the new one encodes, and "which cut was
        this" stays answerable per version.
        """
        draft = await self.draft(workspace_id=workspace_id, project_id=project_id)
        video = draft.timeline.track(TrackType.VIDEO)
        if video is None or not video.items:
            raise ValidationError("This timeline has no clips to render.")

        version = await self._renders.next_version(workspace_id, project_id)
        render = Render(
            workspace_id=workspace_id,
            project_id=project_id,
            version=version,
            status=RenderStatus.PENDING,
            timeline_json=store_timeline(draft.timeline, edited=draft.edited),
            width=draft.timeline.canvas.width,
            height=draft.timeline.canvas.height,
            duration_ms=draft.timeline.duration_ms,
        )
        self._session.add(render)
        await self._session.flush()

        job, created = await JobService(self._session, settings=self._settings).create(
            workspace_id=workspace_id,
            job_type=JobType.RENDER,
            provider="ffmpeg",
            idempotency_key=f"render:{render.id}",
            project_id=project_id,
            input_json={"render_id": str(render.id), "source": "editor"},
            estimated_cost=0.0,
        )
        logger.info(
            "timeline_rerender_queued",
            extra={"project_id": str(project_id), "render_id": str(render.id)},
        )
        return render, job, created

    async def asset_for_logo(self, *, workspace_id: uuid.UUID, asset_id: uuid.UUID) -> MediaAsset:
        """Check a logo asset belongs to this workspace before it is referenced.

        §11's object-key isolation, applied at the edit rather than at the
        render: a timeline that named another tenant's asset would fail deep
        inside a worker, where the error is a stack trace rather than a message.
        """
        asset = await self._renders.asset(workspace_id, asset_id)
        if asset is None:
            raise NotFoundError("That logo image was not found.")
        return asset


# --- operation application --------------------------------------------------


def _apply_one(timeline: Timeline, operation: EditOperation) -> Timeline:
    match operation:
        case ReorderShots():
            return _reorder(timeline, operation.order)
        case TrimClip():
            return _trim(timeline, operation)
        case SetTrackGain():
            return _set_gain(timeline, operation)
        case SetSubtitleStyle():
            return _set_subtitle_style(timeline, operation)
        case SetLogo():
            # Only reachable for a *clearing* logo op; setting one goes through
            # `apply`, which resolves the asset first.
            return _set_logo(timeline, operation, object_key=None)


def _video_items(timeline: Timeline) -> list[TimelineItem]:
    track = timeline.track(TrackType.VIDEO)
    if track is None or not track.items:
        raise ValidationError("This timeline has no clips.")
    return sorted(track.items, key=lambda item: item.start_ms)


def _replace_track(timeline: Timeline, kind: TrackType, items: list[TimelineItem]) -> Timeline:
    tracks = [track for track in timeline.tracks if track.type is not kind]
    if items:
        tracks.append(Track(type=kind, items=items))
    return Timeline(version=timeline.version, canvas=timeline.canvas, tracks=tracks)


def _reorder(timeline: Timeline, order: list[int]) -> Timeline:
    """P20-T02. `order` must be a permutation of the existing clip indices.

    A permutation rather than a partial list: "move clip 3 to the front" leaves
    the rest ambiguous, and a client that sent four indices for five clips
    would silently drop one.
    """
    items = _video_items(timeline)
    if sorted(order) != list(range(len(items))):
        raise ValidationError(
            "The new order must list every clip exactly once.",
            details={"clips": len(items), "received": len(order)},
        )

    # Start times are reassigned here rather than left to `_resequence`.
    # Position on the timeline *is* `start_ms`, and every reader sorts by it —
    # so a reorder that only permuted the list would be undone by the next
    # sort, which is exactly the bug this line prevents.
    cursor = 0
    reordered: list[TimelineItem] = []
    for index in order:
        original = items[index]
        length = original.duration_ms
        reordered.append(
            original.model_copy(update={"start_ms": cursor, "end_ms": cursor + length})
        )
        cursor += length

    return _replace_track(timeline, TrackType.VIDEO, reordered)


def _trim(timeline: Timeline, operation: TrimClip) -> Timeline:
    items = _video_items(timeline)
    if operation.index >= len(items):
        raise ValidationError(
            "That clip does not exist.",
            details={"index": operation.index, "clips": len(items)},
        )

    updated = list(items)
    original = updated[operation.index]
    updated[operation.index] = original.model_copy(
        update={
            "end_ms": original.start_ms + operation.duration_ms,
            "source_start_ms": operation.source_start_ms,
        }
    )
    return _replace_track(timeline, TrackType.VIDEO, updated)


def _set_gain(timeline: Timeline, operation: SetTrackGain) -> Timeline:
    kind = TrackType.VOICE if operation.track == "VOICE" else TrackType.BGM
    track = timeline.track(kind)
    if track is None or not track.items:
        raise ValidationError(
            f"This timeline has no {operation.track.lower()} track.",
            details={"track": operation.track},
        )
    items = [item.model_copy(update={"gain": operation.gain}) for item in track.items]
    return _replace_track(timeline, kind, items)


def _set_subtitle_style(timeline: Timeline, operation: SetSubtitleStyle) -> Timeline:
    """P20-T04. Style rides on the cues' metadata.

    On the items rather than in a separate field because `Timeline` is the
    render's only input (§33): a style stored anywhere else would have to be
    passed to the worker separately, and then two renders of the same timeline
    could differ.
    """
    track = timeline.track(TrackType.SUBTITLE)
    if track is None or not track.items:
        raise ValidationError("This timeline has no subtitles.")

    style: dict[str, Any] = {}
    if operation.font_size is not None:
        style["font_size"] = operation.font_size
    if operation.color is not None:
        style["color"] = operation.color
    if operation.outline_color is not None:
        style["outline_color"] = operation.outline_color
    if operation.margin_v is not None:
        style["margin_v"] = operation.margin_v
    if operation.enabled is not None:
        style["enabled"] = operation.enabled

    items = [
        item.model_copy(update={"metadata": {**item.metadata, **style}}) for item in track.items
    ]
    return _replace_track(timeline, TrackType.SUBTITLE, items)


def _set_logo(timeline: Timeline, operation: SetLogo, *, object_key: str | None) -> Timeline:
    """P20-T07. The logo is metadata on the VIDEO track's first item.

    Not its own track: §33 fixes the four tracks, and a fifth would be a
    schema change every consumer would have to learn. A logo is an overlay on
    the picture, which is what the VIDEO track is.

    Clearing it (`asset_id=None`) is explicitly supported — §141 says a missing
    logo must never block a render, and that has to include removing one.
    """
    items = _video_items(timeline)
    first = items[0]
    metadata = {key: value for key, value in first.metadata.items() if key != "logo"}

    if operation.asset_id is not None and object_key is not None:
        metadata["logo"] = {
            "asset_id": str(operation.asset_id),
            # The storage key, resolved at edit time. The render plan reads
            # this; carrying only the asset id would make the worker do a
            # database lookup, which §33 exists to avoid.
            "object_key": object_key,
            "position": operation.position.value,
            "opacity": operation.opacity,
            "scale": operation.scale,
        }

    updated = list(items)
    updated[0] = first.model_copy(update={"metadata": metadata})
    return _replace_track(timeline, TrackType.VIDEO, updated)


def _resequence(timeline: Timeline) -> Timeline:
    """Lay the video clips end to end, closing gaps a trim or reorder left.

    Without this, trimming a clip by a second leaves a second of black rather
    than a shorter video — the item's slot shrank but the next one did not
    move. Audio is *not* resequenced: narration is timed against the words, and
    shifting it to follow a picture edit would desynchronise every line.
    """
    video = timeline.track(TrackType.VIDEO)
    if video is None or not video.items:
        return timeline

    cursor = 0
    laid: list[TimelineItem] = []
    for item in sorted(video.items, key=lambda entry: entry.start_ms):
        length = max(item.duration_ms, MIN_CLIP_MS)
        laid.append(item.model_copy(update={"start_ms": cursor, "end_ms": cursor + length}))
        cursor += length

    tracks = [track for track in timeline.tracks if track.type is not TrackType.VIDEO]
    tracks.insert(0, Track(type=TrackType.VIDEO, items=laid))
    return Timeline(version=timeline.version, canvas=timeline.canvas, tracks=tracks)


__all__ = [
    "MAX_GAIN",
    "MIN_CLIP_MS",
    "DraftTimeline",
    "EditOperation",
    "ReorderShots",
    "SetLogo",
    "SetSubtitleStyle",
    "SetTrackGain",
    "TimelineEditor",
    "TrimClip",
]
