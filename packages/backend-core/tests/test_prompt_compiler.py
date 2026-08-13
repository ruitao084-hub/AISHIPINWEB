"""The prompt compiler and §29's identity lock (§19, §29, P8-T06/T07/T08).

§19 opens with a prohibition — never hand a video model a sentence a user typed
— and the whole of this module exists to satisfy it. So the tests here are
mostly about *structure*: that the identity rules are present verbatim when the
lock is on, that they are §19's own words rather than a paraphrase, and that a
shot cannot end up with a prompt nobody assembled.
"""

from __future__ import annotations

from backend_core.domain.enums import PRODUCT_DOMINANT_SHOTS, ShotType
from backend_core.prompts.compiler import (
    IDENTITY_RULES,
    ShotPromptSpec,
    compile_negative_prompt,
    compile_shot_prompt,
    default_identity_lock,
    product_identity_line,
)


def _spec(**overrides: object) -> ShotPromptSpec:
    base: dict[str, object] = {
        "subject": "product centred on a pale oak surface",
        "product_identity": "静音空气净化器; 哑光白; 阳极氧化铝",
        "environment": "sunlit living room",
        "composition": "9:16 frame, product centred low",
        "lighting": "soft daylight from the left",
        "camera": "50mm, three-quarter view",
        "camera_motion": "slow push in",
        "material": "anodised aluminium",
        "style": "clean minimal",
    }
    base.update(overrides)
    return ShotPromptSpec(**base)  # type: ignore[arg-type]


class TestBlockAssembly:
    def test_the_prompt_is_assembled_from_labelled_blocks(self) -> None:
        """§19 names thirteen blocks. A prompt is built from them, not typed."""
        compiled = compile_shot_prompt(_spec())
        for label in (
            "SUBJECT",
            "PRODUCT IDENTITY",
            "ENVIRONMENT",
            "COMPOSITION",
            "LIGHTING",
            "CAMERA",
            "CAMERA MOTION",
            "MATERIAL",
            "STYLE",
        ):
            assert f"{label}:" in compiled.prompt

    def test_subject_comes_first(self) -> None:
        compiled = compile_shot_prompt(_spec())
        assert compiled.prompt.startswith("SUBJECT:")

    def test_empty_blocks_are_omitted_rather_than_left_blank(self) -> None:
        """A labelled empty section reads to a video model as "this does not
        matter", which is the opposite of what an unset field means."""
        compiled = compile_shot_prompt(_spec(lighting="", brand=""))
        assert "LIGHTING:" not in compiled.prompt
        assert "BRAND:" not in compiled.prompt

    def test_the_blocks_are_exposed_so_a_user_can_see_why(self) -> None:
        """A UI that shows an opaque wall of text cannot let anyone edit it
        safely. The block list is what makes the prompt explicable."""
        compiled = compile_shot_prompt(_spec())
        labels = [label for label, _ in compiled.blocks]
        assert labels[0] == "SUBJECT"
        assert "NEGATIVE RULES" in labels

    def test_the_negative_prompt_is_not_inside_the_positive_one(self) -> None:
        """Providers take them as separate parameters; a negative list pasted
        into the prompt reads as a request for those things."""
        compiled = compile_shot_prompt(_spec())
        assert "NEGATIVE RULES:" not in compiled.prompt
        assert compiled.negative_prompt


class TestIdentityLock:
    """§29 — the sharp end."""

    def test_a_locked_shot_carries_the_consistency_rules(self) -> None:
        compiled = compile_shot_prompt(_spec(identity_lock=True))
        assert "CONSISTENCY RULES:" in compiled.prompt

    def test_the_rules_are_the_taskbook_s_own_words(self) -> None:
        """Verbatim, not paraphrased.

        Every rewording is an opportunity to soften one — and "preserve logo
        placement" softened into "keep the branding consistent" is how a
        generated product ends up with its logo on the wrong face.
        """
        compiled = compile_shot_prompt(_spec(identity_lock=True))
        for rule in IDENTITY_RULES:
            assert rule in compiled.prompt

    def test_every_rule_the_taskbook_lists_is_present(self) -> None:
        """§19's list, checked item by item rather than by count."""
        for expected in (
            "keep the exact uploaded product identity",
            "preserve shape",
            "preserve structure",
            "preserve material",
            "preserve logo placement",
            "preserve packaging appearance",
            "do not add components",
            "do not alter visible text",
        ):
            assert expected in IDENTITY_RULES

    def test_an_unlocked_shot_omits_them(self) -> None:
        compiled = compile_shot_prompt(_spec(identity_lock=False))
        assert "CONSISTENCY RULES:" not in compiled.prompt

    def test_identity_negatives_are_conditional_on_the_lock(self) -> None:
        """ "different product" fights a wide lifestyle shot that deliberately
        includes other objects, and protects a macro of the product."""
        locked = compile_negative_prompt(_spec(identity_lock=True))
        unlocked = compile_negative_prompt(_spec(identity_lock=False))
        assert "different product" in locked
        assert "different product" not in unlocked

    def test_generic_negatives_apply_either_way(self) -> None:
        for spec in (_spec(identity_lock=True), _spec(identity_lock=False)):
            negative = compile_negative_prompt(spec)
            assert "blurry" in negative
            assert "watermark" in negative

    def test_text_overlays_are_always_forbidden(self) -> None:
        """The most reliable way a generated clip becomes unusable: invented
        captions no post-process can remove."""
        negative = compile_negative_prompt(_spec(identity_lock=False))
        assert "text overlay" in negative
        assert "caption" in negative

    def test_negatives_are_deduplicated_with_order_preserved(self) -> None:
        """A repeated term reads as emphasis to some models and noise to
        others; neither is what was meant."""
        negative = compile_negative_prompt(_spec(extra_negatives=("blurry", "BLURRY", "smoke")))
        assert negative.count("blurry") == 1
        assert "smoke" in negative

    def test_extra_negatives_are_appended(self) -> None:
        negative = compile_negative_prompt(_spec(extra_negatives=("visible cables",)))
        assert "visible cables" in negative


class TestLockDefaults:
    def test_product_dominant_shots_default_to_locked(self) -> None:
        for shot_type in PRODUCT_DOMINANT_SHOTS:
            assert default_identity_lock(shot_type), shot_type

    def test_a_wide_lifestyle_shot_defaults_to_unlocked(self) -> None:
        """Not a safety compromise: the identity rules constrain composition
        as much as identity, and applying them to a room shot produces stiff
        catalogue footage while protecting forty pixels of product."""
        assert not default_identity_lock(ShotType.LIFESTYLE)

    def test_a_macro_defaults_to_locked(self) -> None:
        """Where the product fills the frame, a drifted shape is unmissable."""
        assert default_identity_lock(ShotType.MACRO)


class TestProductIdentityLine:
    def test_it_describes_the_product_from_the_values_given(self) -> None:
        line = product_identity_line("静音空气净化器", colors=["哑光白"], materials=["阳极氧化铝"])
        assert "静音空气净化器" in line
        assert "哑光白" in line
        assert "阳极氧化铝" in line

    def test_a_product_with_no_verified_detail_is_just_its_name(self) -> None:
        """§13 reaches this far. A prompt asserting "brushed aluminium" about a
        plastic product misrepresents it as surely as a script would — and
        unlike a script, nobody proofreads a prompt before it is sent."""
        assert product_identity_line("静音空气净化器") == "静音空气净化器"

    def test_blank_values_do_not_produce_dangling_separators(self) -> None:
        line = product_identity_line("Widget", colors=["", "  "], materials=[])
        assert line == "Widget"
