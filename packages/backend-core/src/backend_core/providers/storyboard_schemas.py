"""Storyboard schema and the duration validator (§18, §107, P8-T01/T02/T04).

§18's hard constraint is one line:

    所有 Shot 时长之和 ≈ Project duration

`validate_storyboard_duration` is what ≈ means, and the tolerance is tight —
10%, against the script's 35%. The two numbers differ because they measure
different things. A script's word budget is an estimate of how long narration
*will* take; a storyboard's total is what the renderer *will produce*. One is a
forecast, the other is an instruction, and an instruction that is 30% wrong
produces a 39-second video for a 30-second slot.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_core.domain.enums import (
    MAX_SHOT_SECONDS,
    MIN_SHOT_SECONDS,
    STORYBOARD_DURATION_TOLERANCE,
    ShotType,
    TransitionType,
)


class ShotDraft(BaseModel):
    """One shot, as the model returns it (§18).

    Every field §18 lists per shot. `voiceover` and `subtitle` may be empty —
    a purely visual beat is legitimate, and §17 already allows a script section
    with no narration.
    """

    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    shot_type: ShotType = ShotType.CUSTOM
    #: Bounded here rather than only at the total: §18 suggests 2-10s per shot,
    #: and a storyboard that hits its total with one 28-second shot has not
    #: understood the brief.
    duration_seconds: float = Field(ge=MIN_SHOT_SECONDS, le=MAX_SHOT_SECONDS)

    visual_description: str = Field(min_length=1)
    camera: str = ""
    motion: str = ""
    lighting: str = ""
    composition: str = ""
    voiceover: str = ""
    subtitle: str = ""
    transition_in: TransitionType = TransitionType.CUT
    transition_out: TransitionType = TransitionType.CUT
    #: §10.13's `reference_assets`, as roles the caller resolves to real media.
    #: The model names what kind of reference a shot needs; it never picks an
    #: asset id, because it has never seen the asset table.
    reference_roles: list[str] = Field(default_factory=list)


class StoryboardDraft(BaseModel):
    """A complete storyboard (§18)."""

    model_config = ConfigDict(extra="forbid")

    shots: list[ShotDraft] = Field(min_length=1, max_length=40)

    @field_validator("shots")
    @classmethod
    def _sequence_is_contiguous_from_one(cls, shots: list[ShotDraft]) -> list[ShotDraft]:
        """Shots must be numbered 1..n with no gaps.

        The renderer concatenates in sequence order, so a gap is not a
        cosmetic problem — it means either a shot was dropped or two will
        collide. Caught here rather than discovered during a render.
        """
        numbers = [shot.sequence_no for shot in shots]
        if numbers != list(range(1, len(shots) + 1)):
            raise ValueError(
                f"Shots must be numbered 1 to {len(shots)} with no gaps or repeats. Got: {numbers}"
            )
        return shots

    @property
    def total_duration_seconds(self) -> float:
        return round(sum(shot.duration_seconds for shot in self.shots), 2)


#: How much a storyboard's total may differ from the project's duration.
DURATION_TOLERANCE: Final[float] = STORYBOARD_DURATION_TOLERANCE


class DurationMismatchError(ValueError):
    """The shots do not add up to the project's duration (§18)."""


def validate_storyboard_duration(
    total_seconds: float, target_seconds: int, *, tolerance: float = DURATION_TOLERANCE
) -> None:
    """Enforce §18's ≈ constraint, or explain precisely how far off it is.

    Raises rather than returning a bool: this is a rule, and the message names
    both numbers because "the storyboard is 39.5s against a 30s target" is
    actionable where "invalid storyboard" is not.
    """
    if target_seconds <= 0:
        raise DurationMismatchError("The project has no target duration to match.")

    drift = abs(total_seconds - target_seconds) / target_seconds
    if drift > tolerance:
        raise DurationMismatchError(
            f"The shots total {total_seconds:g}s against a {target_seconds}s target "
            f"({drift:.0%} off, tolerance {tolerance:.0%}). Adjust the shot durations."
        )


def fit_shot_durations(durations: list[float], target_seconds: int) -> list[float]:
    """Scale durations to hit the target, keeping their proportions.

    Used when a model returns a sensible *shape* whose total is slightly wrong,
    which is the common case — it is much better at "the hook should be short
    and the product reveal long" than at arithmetic.

    Scaling alone is not enough, and the reason is the per-shot bounds. Scaling
    [3, 7, 4] onto a 30s target wants [6.4, 15, 8.6]; the middle value clamps
    to 10 and five seconds vanish, leaving a 25s storyboard that looks fitted
    and is not. So after scaling, the remainder is **redistributed across the
    shots that still have headroom**, repeatedly, until it is absorbed or
    nothing can absorb it.

    When the target genuinely cannot be reached — five shots cannot fill sixty
    seconds at ten seconds each — the result is as close as the bounds allow
    and `validate_storyboard_duration` rejects it. That is the correct
    outcome: the storyboard needs a different number of shots, and silently
    returning a wrong total would hide that.
    """
    if not durations or target_seconds <= 0:
        return durations

    total = sum(durations)
    if total <= 0:
        # Nothing to preserve the proportions *of*; split the target evenly,
        # still within bounds.
        even = min(MAX_SHOT_SECONDS, max(MIN_SHOT_SECONDS, target_seconds / len(durations)))
        return [round(even, 2)] * len(durations)

    ratio = target_seconds / total
    scaled = [_clamp(value * ratio) for value in durations]
    return _absorb_remainder(scaled, target_seconds)


def _clamp(value: float) -> float:
    return min(MAX_SHOT_SECONDS, max(MIN_SHOT_SECONDS, round(value, 2)))


def _absorb_remainder(durations: list[float], target_seconds: int) -> list[float]:
    """Push whatever the scaling lost onto shots that can still take it.

    Water-filling rather than dumping it on one shot: spreading two seconds
    across four shots is invisible, and adding two seconds to one shot changes
    the pacing the model chose. Bounded iteration because each pass either
    absorbs some of the remainder or runs out of headroom, and an unbounded
    loop on a pathological input is worse than an imperfect fit.
    """
    values = list(durations)
    for _ in range(len(values) + 2):
        drift = round(target_seconds - sum(values), 2)
        if abs(drift) < 0.01:
            break

        # Shots that can move in the direction the drift needs.
        movable = [
            index
            for index, value in enumerate(values)
            if (drift > 0 and value < MAX_SHOT_SECONDS) or (drift < 0 and value > MIN_SHOT_SECONDS)
        ]
        if not movable:
            break

        share = drift / len(movable)
        for index in movable:
            values[index] = _clamp(values[index] + share)

    return [round(value, 2) for value in values]


def suggested_shot_count(target_seconds: int) -> int:
    """How many shots a video of this length wants (§18).

    Derived from the per-shot bounds rather than picked: enough that no shot
    has to exceed the maximum, and few enough that none must fall below the
    minimum. The midpoint of that range is the natural pacing.
    """
    if target_seconds <= 0:
        return 1
    fewest = max(1, int(-(-target_seconds // MAX_SHOT_SECONDS)))
    most = max(1, int(target_seconds // MIN_SHOT_SECONDS))
    return max(1, (fewest + most) // 2)


__all__ = [
    "DURATION_TOLERANCE",
    "DurationMismatchError",
    "ShotDraft",
    "StoryboardDraft",
    "fit_shot_durations",
    "suggested_shot_count",
    "validate_storyboard_duration",
]
