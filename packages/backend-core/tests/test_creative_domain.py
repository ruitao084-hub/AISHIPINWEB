"""Project state machine, creative schemas and the character budget (§105, §16, §17).

Pure domain logic, no database. The project machine is derived rather than
hand-written, so these tests check the *properties* the derivation is supposed
to guarantee — that is what catches a wrong rule, whereas asserting the table
back at itself would only restate it.
"""

from __future__ import annotations

import pytest

from backend_core.domain.enums import (
    PLATFORM_DEFAULT_ASPECT,
    SCRIPT_SECTIONS,
    AspectRatio,
    ProjectStatus,
    TargetPlatform,
    allowed_project_transitions,
    can_transition_project,
)
from backend_core.providers.creative_schemas import (
    CreativePlanDraft,
    CreativePlanSet,
    ScriptDocument,
    ScriptSection,
    character_budget,
    duration_fits,
)


def _plan(title: str) -> CreativePlanDraft:
    return CreativePlanDraft(
        title=title,
        concept="c",
        hook="h",
        core_message="m",
        narrative_structure="n",
        visual_direction="v",
        camera_direction="cam",
        music_direction="mus",
        ending_cta="cta",
    )


def _sections(narration: str = "一句旁白。") -> list[ScriptSection]:
    return [ScriptSection(section=name, narration=narration) for name in SCRIPT_SECTIONS]


class TestProjectTransitions:
    def test_the_pipeline_runs_forward_one_stage_at_a_time(self) -> None:
        assert can_transition_project(ProjectStatus.DRAFT, ProjectStatus.ANALYZING)
        assert can_transition_project(ProjectStatus.CREATIVE_PLANNING, ProjectStatus.SCRIPTING)
        assert can_transition_project(ProjectStatus.QC, ProjectStatus.READY)

    def test_the_analysis_stage_can_be_skipped(self) -> None:
        """The one skippable stage, and it has to be.

        A project's `ANALYZING` means "analyse the product this is for", and
        PHASE 6 made that a product-level concern. A product already analysed
        would otherwise force its projects through a stage that either bills a
        second vision call or is set and immediately cleared.
        """
        assert can_transition_project(ProjectStatus.DRAFT, ProjectStatus.CREATIVE_PLANNING)
        assert can_transition_project(ProjectStatus.CREATIVE_PLANNING, ProjectStatus.DRAFT)

    def test_stages_cannot_be_skipped(self) -> None:
        """The rule the machine exists for: a project cannot arrive at
        GENERATING without a storyboard to generate from."""
        assert not can_transition_project(ProjectStatus.DRAFT, ProjectStatus.GENERATING)
        assert not can_transition_project(ProjectStatus.SCRIPTING, ProjectStatus.READY)

    def test_a_user_can_go_back_one_stage(self) -> None:
        """§103 rule 4. Rejecting a storyboard and rewriting the script is the
        ordinary case, not an exception."""
        assert can_transition_project(ProjectStatus.STORYBOARDING, ProjectStatus.SCRIPTING)
        assert can_transition_project(ProjectStatus.SCRIPTING, ProjectStatus.CREATIVE_PLANNING)

    def test_failure_is_reachable_from_every_working_stage(self) -> None:
        """§105: FAILED enters from most intermediate states."""
        for status in (
            ProjectStatus.ANALYZING,
            ProjectStatus.CREATIVE_PLANNING,
            ProjectStatus.SCRIPTING,
            ProjectStatus.STORYBOARDING,
            ProjectStatus.GENERATING,
            ProjectStatus.COMPOSITING,
            ProjectStatus.QC,
        ):
            assert can_transition_project(status, ProjectStatus.FAILED), status

    def test_a_draft_cannot_fail(self) -> None:
        """Nothing has run yet, so there is nothing to have failed."""
        assert not can_transition_project(ProjectStatus.DRAFT, ProjectStatus.FAILED)

    def test_recovery_returns_to_the_stage_that_failed(self) -> None:
        """§105's 恢复后回原合理状态 — not back to the beginning."""
        recoverable = allowed_project_transitions(ProjectStatus.FAILED)
        assert ProjectStatus.GENERATING in recoverable
        assert ProjectStatus.SCRIPTING in recoverable
        # Not to DRAFT: a project that failed during rendering has creative
        # work behind it that recovery must not discard.
        assert ProjectStatus.DRAFT not in recoverable

    def test_a_finished_project_can_be_sent_back_for_another_pass(self) -> None:
        """§103 rule 10's one-click regeneration has to land somewhere."""
        assert can_transition_project(ProjectStatus.READY, ProjectStatus.STORYBOARDING)

    def test_archive_is_reachable_from_everywhere_and_is_terminal(self) -> None:
        for status in ProjectStatus:
            if status is ProjectStatus.ARCHIVED:
                continue
            assert can_transition_project(status, ProjectStatus.ARCHIVED), status
        assert allowed_project_transitions(ProjectStatus.ARCHIVED) == frozenset()

    def test_a_status_can_always_be_written_to_itself(self) -> None:
        """Idempotent retries must not be errors."""
        for status in ProjectStatus:
            assert can_transition_project(status, status)

    def test_every_status_has_an_entry(self) -> None:
        """A missing key would be a KeyError at runtime, in a transition."""
        for status in ProjectStatus:
            assert isinstance(allowed_project_transitions(status), frozenset)


class TestPlatformDefaults:
    def test_every_platform_has_a_native_frame(self) -> None:
        for platform in TargetPlatform:
            assert platform in PLATFORM_DEFAULT_ASPECT

    def test_short_video_platforms_default_to_portrait(self) -> None:
        assert PLATFORM_DEFAULT_ASPECT[TargetPlatform.DOUYIN] is AspectRatio.PORTRAIT_9_16
        assert PLATFORM_DEFAULT_ASPECT[TargetPlatform.TIKTOK] is AspectRatio.PORTRAIT_9_16

    def test_youtube_defaults_to_landscape(self) -> None:
        assert PLATFORM_DEFAULT_ASPECT[TargetPlatform.YOUTUBE] is AspectRatio.LANDSCAPE_16_9


class TestCreativePlanSet:
    def test_exactly_three_plans_are_required(self) -> None:
        """§16 promises the user a choice of three."""
        with pytest.raises(ValueError):
            CreativePlanSet(plans=[_plan("a"), _plan("b")])
        with pytest.raises(ValueError):
            CreativePlanSet(plans=[_plan("a"), _plan("b"), _plan("c"), _plan("d")])

    def test_three_distinct_plans_are_accepted(self) -> None:
        assert len(CreativePlanSet(plans=[_plan("a"), _plan("b"), _plan("c")]).plans) == 3

    def test_duplicate_titles_are_rejected(self) -> None:
        """Three paraphrases of one idea is one plan, not three.

        A model asked for variations will produce near-duplicates unless
        something stops it; a title collision is the cheapest signal that it
        did, and the generation is worth re-running rather than showing.
        """
        with pytest.raises(ValueError, match="distinct"):
            CreativePlanSet(plans=[_plan("同一个想法"), _plan("同一个想法"), _plan("另一个")])

    def test_title_comparison_ignores_case_and_padding(self) -> None:
        with pytest.raises(ValueError, match="distinct"):
            CreativePlanSet(plans=[_plan("Same Idea"), _plan("  same idea "), _plan("other")])

    def test_a_plan_missing_its_hook_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            CreativePlanDraft.model_validate({"title": "t", "concept": "c"})

    def test_an_invented_field_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            CreativePlanSet.model_validate(
                {"plans": [_plan("a").model_dump() | {"budget": "$50k"}] * 3}
            )


class TestScriptDocument:
    def test_all_nine_sections_in_order_are_accepted(self) -> None:
        assert len(ScriptDocument(sections=_sections()).sections) == len(SCRIPT_SECTIONS)

    def test_a_missing_section_is_rejected(self) -> None:
        """§17 names nine. A script with a hole where the evidence should be
        would produce a storyboard with the same hole."""
        with pytest.raises(ValueError):
            ScriptDocument(sections=_sections()[:-1])

    def test_sections_out_of_order_are_rejected(self) -> None:
        """PHASE 8 turns this sequence into shots, so order is structural — a
        CTA before the hook is a video nobody could film."""
        scrambled = list(reversed(_sections()))
        with pytest.raises(ValueError, match="in order"):
            ScriptDocument(sections=scrambled)

    def test_a_duplicated_section_is_rejected(self) -> None:
        sections = _sections()
        sections[1] = ScriptSection(section="opening_hook", narration="x")
        with pytest.raises(ValueError):
            ScriptDocument(sections=sections)

    def test_an_unknown_section_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown script section"):
            ScriptSection(section="mid_roll_ad")

    def test_plain_text_is_the_narration_in_order(self) -> None:
        document = ScriptDocument(
            sections=[
                ScriptSection(section=name, narration=f"第{index}句")
                for index, name in enumerate(SCRIPT_SECTIONS)
            ]
        )
        assert document.plain_text.splitlines()[0] == "第0句"
        assert len(document.plain_text.splitlines()) == len(SCRIPT_SECTIONS)

    def test_a_silent_section_is_allowed_and_omitted_from_the_text(self) -> None:
        """A purely visual beat is a legitimate script, and its blank line
        would otherwise reach the TTS engine as a pause nobody asked for."""
        sections = _sections()
        sections[6] = ScriptSection(section="proof_or_visual_support", narration="", visual="演示")
        document = ScriptDocument(sections=sections)
        assert len(document.plain_text.splitlines()) == len(SCRIPT_SECTIONS) - 1


class TestCharacterBudget:
    def test_a_longer_video_gets_a_bigger_budget(self) -> None:
        assert character_budget(60) > character_budget(15)

    def test_the_budget_is_floored_not_rounded_up(self) -> None:
        """A script that runs long has to be cut, and cutting is the edit
        users resent most."""
        assert character_budget(1) == 4  # 1 * 4.5 -> 4, not 5

    def test_whitespace_does_not_count_toward_narration_length(self) -> None:
        """Chinese narration has almost no spaces and English has a great deal;
        counting them would make one budget mean two different things."""
        spaced = ScriptDocument(
            sections=[ScriptSection(section=name, narration="a b c") for name in SCRIPT_SECTIONS]
        )
        assert spaced.narration_characters == 3 * len(SCRIPT_SECTIONS)

    def test_the_duration_estimate_tracks_the_narration_length(self) -> None:
        short = ScriptDocument(sections=_sections("短。"))
        long = ScriptDocument(sections=_sections("这是一句明显更长的旁白，用来测试时长估算。"))
        assert long.estimated_duration_seconds() > short.estimated_duration_seconds()

    def test_a_script_near_its_target_fits(self) -> None:
        assert duration_fits(30.0, 30)
        assert duration_fits(34.0, 30)

    def test_a_script_far_from_its_target_does_not(self) -> None:
        assert not duration_fits(60.0, 30)
        assert not duration_fits(5.0, 30)

    def test_a_zero_target_never_fits(self) -> None:
        """Guards the division. A project cannot have zero duration, but the
        helper should not divide by zero if one ever did."""
        assert not duration_fits(10.0, 0)
