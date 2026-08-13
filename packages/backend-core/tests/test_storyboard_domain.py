"""Storyboard schema and the duration validator (§18, P8-T01/T02/T04).

§18's constraint is one line — the shots must sum to the project's duration —
and most of this file is about the two things that make it hold: a validator
that refuses anything outside tolerance, and a fitter that rescues the common
case where a model got the *pacing* right and the arithmetic wrong.
"""

from __future__ import annotations

import pytest

from backend_core.domain.enums import MAX_SHOT_SECONDS, MIN_SHOT_SECONDS, ShotType
from backend_core.providers.storyboard_schemas import (
    DURATION_TOLERANCE,
    DurationMismatchError,
    ShotDraft,
    StoryboardDraft,
    fit_shot_durations,
    suggested_shot_count,
    validate_storyboard_duration,
)


def _shot(sequence_no: int, duration: float = 5.0) -> ShotDraft:
    return ShotDraft(
        sequence_no=sequence_no,
        duration_seconds=duration,
        visual_description=f"shot {sequence_no}",
        shot_type=ShotType.PRODUCT_HERO,
    )


class TestStoryboardDraft:
    def test_a_contiguous_sequence_is_accepted(self) -> None:
        draft = StoryboardDraft(shots=[_shot(1), _shot(2), _shot(3)])
        assert draft.total_duration_seconds == 15.0

    def test_a_gap_in_the_sequence_is_rejected(self) -> None:
        """The renderer concatenates in sequence order, so a gap means a shot
        was dropped — caught here rather than during a render."""
        with pytest.raises(ValueError, match="no gaps"):
            StoryboardDraft(shots=[_shot(1), _shot(3)])

    def test_a_repeated_sequence_number_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="no gaps"):
            StoryboardDraft(shots=[_shot(1), _shot(1)])

    def test_a_sequence_not_starting_at_one_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            StoryboardDraft(shots=[_shot(2), _shot(3)])

    def test_a_shot_shorter_than_the_minimum_is_rejected(self) -> None:
        """§18: below two seconds a shot reads as a flicker."""
        with pytest.raises(ValueError):
            ShotDraft(sequence_no=1, duration_seconds=0.5, visual_description="x")

    def test_a_shot_longer_than_the_maximum_is_rejected(self) -> None:
        """Above ten seconds most video models lose coherence and drift."""
        with pytest.raises(ValueError):
            ShotDraft(sequence_no=1, duration_seconds=25, visual_description="x")

    def test_a_shot_needs_a_visual_description(self) -> None:
        """It becomes a generation prompt; an empty one has nothing to send."""
        with pytest.raises(ValueError):
            ShotDraft(sequence_no=1, duration_seconds=5, visual_description="")

    def test_an_invented_field_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            StoryboardDraft.model_validate({"shots": [_shot(1).model_dump() | {"budget": "$1000"}]})

    def test_an_empty_storyboard_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            StoryboardDraft(shots=[])


class TestDurationValidator:
    def test_an_exact_match_passes(self) -> None:
        validate_storyboard_duration(30.0, 30)

    def test_a_small_drift_passes(self) -> None:
        validate_storyboard_duration(31.0, 30)

    def test_a_large_drift_is_refused(self) -> None:
        with pytest.raises(DurationMismatchError):
            validate_storyboard_duration(39.5, 30)

    def test_the_message_names_both_numbers(self) -> None:
        """ "The storyboard is 39.5s against a 30s target" is actionable;
        "invalid storyboard" is not."""
        with pytest.raises(DurationMismatchError) as caught:
            validate_storyboard_duration(39.5, 30)
        assert "39.5" in str(caught.value)
        assert "30" in str(caught.value)

    def test_the_tolerance_is_tighter_than_the_script_s(self) -> None:
        """They measure different things. A script's word budget forecasts how
        long narration *will* take; a storyboard's total is what the renderer
        *will produce* — a forecast may be loose, an instruction may not.
        """
        from backend_core.providers.creative_schemas import (
            DURATION_TOLERANCE as SCRIPT_TOLERANCE,
        )

        assert DURATION_TOLERANCE < SCRIPT_TOLERANCE

    def test_a_zero_target_is_refused_rather_than_dividing_by_zero(self) -> None:
        with pytest.raises(DurationMismatchError):
            validate_storyboard_duration(10.0, 0)


class TestDurationFitting:
    def test_it_hits_the_target_exactly(self) -> None:
        fitted = fit_shot_durations([3.0, 7.0, 4.0], 30)
        assert sum(fitted) == pytest.approx(30.0, abs=0.05)

    def test_it_preserves_the_pacing_the_model_chose(self) -> None:
        """A model is good at "short hook, long reveal" and bad at making
        eight numbers sum to thirty. Scaling keeps the judgement.

        Fifteen seconds, not thirty: two shots cannot exceed twenty, and at
        the ceiling every shot is the same length, so there is no pacing left
        to preserve. That case is covered by the unreachable-target test.
        """
        fitted = fit_shot_durations([2.0, 8.0], 15)
        assert sum(fitted) == pytest.approx(15.0, abs=0.05)
        assert fitted[1] > fitted[0]

    def test_every_result_stays_within_the_per_shot_bounds(self) -> None:
        fitted = fit_shot_durations([1.0, 1.0, 1.0, 1.0], 60)
        for value in fitted:
            assert MIN_SHOT_SECONDS <= value <= MAX_SHOT_SECONDS

    def test_it_absorbs_the_remainder_clamping_would_otherwise_lose(self) -> None:
        """The bug this function had, pinned.

        Scaling [3, 7, 4] onto 30s wants [6.4, 15, 8.6]. The middle value
        clamps to 10 and five seconds vanish, leaving a 25s storyboard that
        looks fitted and is not — so the remainder is redistributed across the
        shots that still have headroom.
        """
        fitted = fit_shot_durations([3.0, 7.0, 4.0], 30)
        assert sum(fitted) == pytest.approx(30.0, abs=0.05)

    def test_a_fitted_storyboard_passes_the_validator(self) -> None:
        """The two halves working together, which is what actually matters.

        Shot counts here are ones that can reach the target: five shots max out
        at fifty seconds, so a sixty-second target needs more of them.
        """
        for target, count in ((15, 5), (30, 5), (45, 6), (60, 8)):
            durations = [4.0, 6.0, 3.0, 5.0, 7.0, 2.0, 8.0, 5.0][:count]
            fitted = fit_shot_durations(durations, target)
            validate_storyboard_duration(round(sum(fitted), 2), target)

    def test_an_unreachable_target_lands_short_and_is_then_rejected(self) -> None:
        """Five shots cannot fill sixty seconds at ten seconds each.

        The fitter gets as close as the bounds allow and the validator refuses
        it — which is correct. The storyboard needs a different *number* of
        shots, and silently returning a wrong total would hide that.
        """
        fitted = fit_shot_durations([4.0, 6.0, 3.0, 5.0, 7.0], 60)
        assert all(value == MAX_SHOT_SECONDS for value in fitted)
        with pytest.raises(DurationMismatchError):
            validate_storyboard_duration(round(sum(fitted), 2), 60)

    def test_the_suggested_shot_count_can_always_reach_its_target(self) -> None:
        """The property that keeps the two functions honest together: the count
        the storyboard prompt asks for must be one the fitter can satisfy."""
        for target in (10, 15, 30, 45, 60, 120):
            count = suggested_shot_count(target)
            fitted = fit_shot_durations([5.0] * count, target)
            validate_storyboard_duration(round(sum(fitted), 2), target)

    def test_all_zero_durations_are_split_evenly(self) -> None:
        """No proportions to preserve, so there is nothing to scale."""
        fitted = fit_shot_durations([0.0, 0.0, 0.0], 30)
        assert fitted == [10.0, 10.0, 10.0]

    def test_an_empty_list_is_returned_unchanged(self) -> None:
        assert fit_shot_durations([], 30) == []


class TestShotCount:
    def test_a_longer_video_wants_more_shots(self) -> None:
        assert suggested_shot_count(60) > suggested_shot_count(15)

    def test_the_suggestion_is_achievable_within_the_bounds(self) -> None:
        """The count has to be one where every shot can legally hit its share
        — otherwise the model is asked for something the schema will reject."""
        for target in (10, 15, 30, 45, 60, 120):
            count = suggested_shot_count(target)
            per_shot = target / count
            assert MIN_SHOT_SECONDS <= per_shot <= MAX_SHOT_SECONDS, (target, count)

    def test_a_zero_duration_still_returns_at_least_one(self) -> None:
        assert suggested_shot_count(0) >= 1
