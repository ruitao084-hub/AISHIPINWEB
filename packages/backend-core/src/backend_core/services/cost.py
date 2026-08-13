"""What a generation will cost, before it runs (§95, P18-T06, P18-T07).

PHASE 9 estimated cost with a one-line multiplier and a comment saying PHASE 18
would replace it. This is that replacement.

The design decision worth stating: **the estimate is a range, not a number.**
Video providers price per second of *output*, and a five-second request can
return 4.8 or 5.2 seconds; a retry costs again; a provider's minimum billing
increment rounds up. A single number would be wrong in one direction or the
other every time, and the direction users notice is "you said 40 and charged
52". A range that contains the answer is more useful than a point estimate that
does not.

Rates live in one table rather than scattered through the services that spend
money, so "what does this platform charge" is answerable by reading one file.
They are credits, not currency: §95 meters in credits and the exchange rate is
a billing concern, not a generation one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend_core.domain.enums import JobType, QualityMode

#: Credits per second of generated video, by quality tier. The spread reflects
#: what providers actually charge — a premium model is several times the cost
#: of a fast one for the same duration, not a few percent more.
VIDEO_RATE_PER_SECOND: Final[dict[QualityMode, float]] = {
    QualityMode.FAST: 0.5,
    QualityMode.STANDARD: 1.0,
    QualityMode.HIGH: 2.0,
    QualityMode.PREMIUM: 4.0,
}

#: Narration. Cheap per second and charged per character by most vendors;
#: expressed per second here so a caller estimates from a duration rather than
#: from a character count it does not have until the script is written.
TTS_RATE_PER_SECOND: Final[float] = 0.05

#: Composition. Flat, plus a small per-second term: ffmpeg time scales with
#: output length, and a 60-second video costs meaningfully more CPU than a 15.
RENDER_BASE: Final[float] = 1.0
RENDER_RATE_PER_SECOND: Final[float] = 0.02

#: One vision call over the product's images.
ANALYSIS_BASE: Final[float] = 2.0

#: Quality checks, when enabled. The technical half is free — it is our own
#: ffprobe — so this covers the visual half's vision call (§37.2).
QC_BASE: Final[float] = 1.5

#: How much a provider's actual output can exceed the request. Providers round
#: to their own increments and overshoot slightly; 15% covers what has been
#: observed without inflating every quote.
OVERRUN_FACTOR: Final[float] = 1.15


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A quote for one piece of work (P18-T06).

    `expected` is what to display; `maximum` is what to reserve. Reserving the
    maximum is what stops a job starting that the workspace cannot finish
    paying for — the alternative is discovering it at capture, by which point
    the provider has already been paid.
    """

    expected: float
    maximum: float
    #: Human-readable breakdown, so a confirmation dialog can say *why*.
    lines: tuple[tuple[str, float], ...] = ()

    def __add__(self, other: CostEstimate) -> CostEstimate:
        return CostEstimate(
            expected=round(self.expected + other.expected, 2),
            maximum=round(self.maximum + other.maximum, 2),
            lines=(*self.lines, *other.lines),
        )


def _quote(expected: float, lines: tuple[tuple[str, float], ...]) -> CostEstimate:
    return CostEstimate(
        expected=round(expected, 2),
        maximum=round(expected * OVERRUN_FACTOR, 2),
        lines=lines,
    )


def estimate_shot(duration_seconds: float, quality: QualityMode) -> CostEstimate:
    """One shot's generation (P18-T06)."""
    rate = VIDEO_RATE_PER_SECOND.get(quality, VIDEO_RATE_PER_SECOND[QualityMode.STANDARD])
    cost = duration_seconds * rate
    return _quote(cost, ((f"{duration_seconds:g}s at {quality.value.lower()} quality", cost),))


def estimate_storyboard(
    shot_durations: list[float], quality: QualityMode, *, retries: int = 0
) -> CostEstimate:
    """Every shot of a storyboard.

    `retries` inflates the maximum rather than the expected value. Most shots
    succeed first time, so charging the retry allowance as the headline number
    would quote roughly double what people actually pay — but a reservation
    that ignored retries entirely would run out mid-storyboard.
    """
    rate = VIDEO_RATE_PER_SECOND.get(quality, VIDEO_RATE_PER_SECOND[QualityMode.STANDARD])
    total_seconds = sum(shot_durations)
    expected = total_seconds * rate

    return CostEstimate(
        expected=round(expected, 2),
        maximum=round(expected * OVERRUN_FACTOR * (1 + retries * 0.5), 2),
        lines=(
            (
                f"{len(shot_durations)} shots, {total_seconds:g}s total "
                f"at {quality.value.lower()} quality",
                round(expected, 2),
            ),
        ),
    )


def estimate_voiceover(duration_seconds: float) -> CostEstimate:
    cost = duration_seconds * TTS_RATE_PER_SECOND
    return _quote(cost, ((f"Narration, {duration_seconds:g}s", cost),))


def estimate_render(duration_seconds: float) -> CostEstimate:
    cost = RENDER_BASE + duration_seconds * RENDER_RATE_PER_SECOND
    return _quote(cost, (("Composition and encoding", cost),))


def estimate_analysis(image_count: int) -> CostEstimate:
    """Product analysis. Flat: one vision call carries all the images."""
    cost = ANALYSIS_BASE
    return _quote(cost, ((f"Vision analysis of {image_count} images", cost),))


def estimate_quality_check() -> CostEstimate:
    return _quote(QC_BASE, (("Quality checks", QC_BASE),))


def estimate_project(
    *, shot_durations: list[float], quality: QualityMode, narration_seconds: float, with_qc: bool
) -> CostEstimate:
    """A whole project, end to end (P18-T07).

    What the confirmation dialog shows before anyone presses Generate. Built by
    summing the individual quotes rather than with its own formula, so the
    total and the per-step numbers cannot disagree — which they would within a
    month if each were maintained separately.
    """
    total = estimate_storyboard(shot_durations, quality)
    if narration_seconds > 0:
        total = total + estimate_voiceover(narration_seconds)
    total = total + estimate_render(sum(shot_durations))
    if with_qc:
        total = total + estimate_quality_check()
    return total


def estimate_for_job(
    job_type: JobType, *, duration_seconds: float = 0.0, quality: QualityMode | None = None
) -> CostEstimate:
    """Dispatch by job type, for the orchestrator's reservation."""
    resolved = quality or QualityMode.STANDARD
    match job_type:
        case JobType.VIDEO_GENERATION | JobType.IMAGE_GENERATION:
            return estimate_shot(duration_seconds, resolved)
        case JobType.TTS:
            return estimate_voiceover(duration_seconds)
        case JobType.RENDER:
            return estimate_render(duration_seconds)
        case JobType.QC:
            return estimate_quality_check()
        case JobType.PRODUCT_ANALYSIS:
            return estimate_analysis(0)


__all__ = [
    "ANALYSIS_BASE",
    "OVERRUN_FACTOR",
    "QC_BASE",
    "RENDER_BASE",
    "TTS_RATE_PER_SECOND",
    "VIDEO_RATE_PER_SECOND",
    "CostEstimate",
    "estimate_analysis",
    "estimate_for_job",
    "estimate_project",
    "estimate_quality_check",
    "estimate_render",
    "estimate_shot",
    "estimate_storyboard",
    "estimate_voiceover",
]
