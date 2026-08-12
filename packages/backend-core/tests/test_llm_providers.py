"""LLM provider contract, mock and selection (§16, §17, §107, §170, §172).

The assertion that matters most here is negative: **the mock never invents a
product fact.** Given a brief with no verified claims it says so in
`risk_notes` rather than filling the gap, because the mock is what the review
UI and every downstream phase are built against — a mock that fabricated would
teach the whole product to expect fabrication.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend_core.config import Settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.providers.base import CreativeBrief, LLMProvider
from backend_core.providers.creative_schemas import character_budget
from backend_core.providers.mock_llm import MockLLMProvider
from backend_core.providers.registry import get_llm_provider


def document_sections(result: Any) -> Any:
    """The generated script's sections, in the order the model returned them."""
    return result.document.sections


def _settings(**overrides: Any) -> Settings:
    return Settings(jwt_secret="x" * 32, **overrides)


def _brief(**overrides: Any) -> CreativeBrief:
    base: dict[str, Any] = {
        "product_name": "静音空气净化器",
        "category": "家用电器",
        "verified_facts": ["materials: 阳极氧化铝", "structural_features: 圆角矩形机身"],
        "verified_claims": ["外观简洁，容易融入家居环境"],
        "visual_dna": {"tone": ["干净", "现代"], "recommended_backgrounds": ["素色墙面"]},
        "duration_seconds": 30,
    }
    base.update(overrides)
    return CreativeBrief(**base)


class TestCreativePlans:
    def test_it_returns_exactly_three_distinct_directions(self) -> None:
        result = MockLLMProvider(_settings()).generate_creative_plans(_brief())
        titles = [plan.title for plan in result.plans.plans]
        assert len(titles) == 3
        assert len(set(titles)) == 3

    def test_it_is_deterministic(self) -> None:
        provider = MockLLMProvider(_settings())
        first = provider.generate_creative_plans(_brief())
        second = provider.generate_creative_plans(_brief())
        assert first.plans == second.plans

    def test_it_records_the_prompt_it_used(self) -> None:
        """§15 — key and version on every call, mock included."""
        result = MockLLMProvider(_settings()).generate_creative_plans(_brief())
        assert result.prompt_key == "creative_plan_v1"
        assert result.prompt_version >= 1

    def test_it_reports_cost_metadata(self) -> None:
        result = MockLLMProvider(_settings()).generate_creative_plans(_brief())
        assert result.usage.input_tokens is not None
        assert result.usage.latency_ms is not None

    def test_the_core_message_comes_from_a_verified_claim(self) -> None:
        """§13 — the only marketing statements available are approved ones."""
        claim = "外观简洁，容易融入家居环境"
        result = MockLLMProvider(_settings()).generate_creative_plans(
            _brief(verified_claims=[claim])
        )
        assert all(plan.core_message == claim for plan in result.plans.plans)
        assert all(plan.risk_notes == "" for plan in result.plans.plans)

    def test_with_no_approved_claims_it_says_so_instead_of_inventing_one(self) -> None:
        """The single most important behaviour in this file.

        A creative model with nothing to say about a product will happily make
        something up. The mock must not, because every downstream phase is
        developed against it — and `risk_notes` is where §16 puts exactly this.
        """
        result = MockLLMProvider(_settings()).generate_creative_plans(_brief(verified_claims=[]))
        for plan in result.plans.plans:
            assert plan.risk_notes
            assert "已核实" in plan.risk_notes

    def test_the_plan_reflects_the_requested_duration(self) -> None:
        short = MockLLMProvider(_settings()).generate_creative_plans(_brief(duration_seconds=15))
        assert "15 秒" in short.plans.plans[0].narrative_structure


class TestScripts:
    def _script(self, **overrides: Any) -> Any:
        provider = MockLLMProvider(_settings())
        brief = _brief(**overrides)
        plan = provider.generate_creative_plans(brief).plans.plans[0]
        return provider.generate_script(
            brief, plan, character_budget=character_budget(brief.duration_seconds)
        )

    def test_it_produces_every_section_in_order(self) -> None:
        """`ScriptDocument` enforces this; constructing one proves the mock
        satisfies it rather than only claiming to."""
        names = [section.section for section in document_sections(self._script())]
        assert names[0] == "opening_hook"
        assert names[-1] == "cta"

    def test_a_shorter_video_produces_a_materially_shorter_script(self) -> None:
        """The budget has to actually bind, or §17's word budget is decoration
        and PHASE 12 discovers the problem after paying for narration."""
        short = self._script(duration_seconds=15).document
        long = self._script(duration_seconds=60).document
        assert long.narration_characters > short.narration_characters * 1.5

    def test_it_stays_within_the_budget_it_was_given(self) -> None:
        for seconds in (15, 30, 60):
            document = self._script(duration_seconds=seconds).document
            assert document.narration_characters <= character_budget(seconds)

    def test_features_come_from_verified_facts(self) -> None:
        document = self._script(
            verified_facts=["materials: 阳极氧化铝", "structural_features: 圆角矩形机身"]
        ).document
        feature = next(s for s in document.sections if s.section == "feature_1")
        assert "阳极氧化铝" in feature.narration

    def test_with_no_verified_facts_the_feature_beats_stay_silent(self) -> None:
        """Silence over invention (§13). A purely visual beat is a legitimate
        script; a sentence about a product nobody described is not.
        """
        document = self._script(verified_facts=[]).document
        for name in ("feature_1", "feature_2"):
            section = next(s for s in document.sections if s.section == name)
            assert section.narration == ""

    def test_the_proof_beat_asserts_nothing_without_an_approved_claim(self) -> None:
        """§17's `proof_or_visual_support` is where an unbacked superlative
        would most naturally appear, so it is the beat worth pinning."""
        document = self._script(verified_claims=[]).document
        proof = next(s for s in document.sections if s.section == "proof_or_visual_support")
        assert proof.narration == ""
        assert proof.visual

    def test_it_records_the_prompt_it_used(self) -> None:
        result = self._script()
        assert result.prompt_key == "script_generate_v1"
        assert result.prompt_version >= 1


class TestFailureInjection:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("unavailable", ProviderUnavailableError),
            ("rate_limited", ProviderRateLimitedError),
            ("rejected", ProviderRejectedError),
        ],
    )
    def test_transport_failures_raise_their_own_type(
        self, mode: str, expected: type[Exception]
    ) -> None:
        provider = MockLLMProvider(_settings(mock_llm_mode=mode))
        with pytest.raises(expected):
            provider.generate_creative_plans(_brief())

    def test_malformed_output_is_not_a_provider_error(self) -> None:
        """§107's case: a 200 whose body fails validation. The call succeeded
        and was billed, so it must stay out of the transport retry path."""
        provider = MockLLMProvider(_settings(mock_llm_mode="malformed"))
        with pytest.raises(ValueError) as caught:
            provider.generate_creative_plans(_brief())
        assert not isinstance(caught.value, ProviderUnavailableError)

    def test_the_script_path_fails_the_same_way(self) -> None:
        """A vendor outage does not distinguish between the two calls."""
        provider = MockLLMProvider(_settings(mock_llm_mode="unavailable"))
        plan = MockLLMProvider(_settings()).generate_creative_plans(_brief()).plans.plans[0]
        with pytest.raises(ProviderUnavailableError):
            provider.generate_script(_brief(), plan, character_budget=135)


class TestSelection:
    def test_mock_providers_flag_wins_over_a_configured_vendor(self) -> None:
        provider = get_llm_provider(
            _settings(
                use_mock_providers=True,
                default_llm_provider="anthropic",
                enable_real_llm_provider=True,
                anthropic_api_key="sk-not-real",
            )
        )
        assert provider.name == "mock"

    def test_a_real_provider_without_its_flag_is_refused(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            get_llm_provider(
                _settings(
                    use_mock_providers=False,
                    default_llm_provider="anthropic",
                    enable_real_llm_provider=False,
                )
            )

    def test_an_unknown_provider_name_is_refused(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            get_llm_provider(
                _settings(
                    use_mock_providers=False,
                    default_llm_provider="nonesuch",
                    enable_real_llm_provider=True,
                )
            )

    def test_a_real_provider_configured_without_a_key_fails_at_boot(self) -> None:
        """Better than failing at the first generation, which costs a user
        their flow rather than a deploy that was going to fail anyway."""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            _settings(
                use_mock_providers=False,
                default_llm_provider="anthropic",
                enable_real_llm_provider=True,
                anthropic_api_key="",
            )

    def test_the_mock_satisfies_the_llm_provider_protocol(self) -> None:
        assert isinstance(MockLLMProvider(_settings()), LLMProvider)
