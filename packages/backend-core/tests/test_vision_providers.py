"""Vision provider contract, mock and selection (§14, §20, §170, §172).

Three things are under test, and only the first is about the mock itself:

1. The mock is deterministic and reaches every injected failure (§172),
   because a mock that returns random content makes its callers' tests flaky
   and a mock that only succeeds leaves the error handling unexercised.
2. The observed/inferred boundary holds at the schema level (§14, §109) —
   this is where the Truth Layer's safety actually lives.
3. Selection is configuration (§0.1 rule 6): asking for a real provider
   without the flag, or for an unknown one, fails loudly rather than silently
   falling back.
"""

from __future__ import annotations

import pytest

from backend_core.config import Settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.providers.base import ProviderImage, VisionProvider
from backend_core.providers.mock_vision import MockVisionProvider
from backend_core.providers.registry import get_vision_provider
from backend_core.providers.schemas import (
    INFERRED_FIELDS,
    OBSERVED_FIELDS,
    ProductIntelligence,
    VisualDNA,
)


def _settings(**overrides: object) -> Settings:
    return Settings(jwt_secret="x" * 32, **overrides)  # type: ignore[arg-type]


def _images(count: int = 2) -> list[ProviderImage]:
    return [
        ProviderImage(data=f"image-{index}".encode(), mime_type="image/jpeg", role="MAIN")
        for index in range(count)
    ]


class TestMockDeterminism:
    def test_the_same_inputs_produce_the_same_analysis(self) -> None:
        """§172's requirement: a mock whose output moves cannot be asserted on."""
        provider = MockVisionProvider(_settings())
        first = provider.analyze_product(_images(), product_name="净化器")
        second = provider.analyze_product(_images(), product_name="净化器")
        assert first.intelligence == second.intelligence

    def test_different_products_produce_different_analyses(self) -> None:
        provider = MockVisionProvider(_settings())
        one = provider.analyze_product(_images(), product_name="净化器")
        two = provider.analyze_product(_images(), product_name="台灯")
        assert one.intelligence.product_name != two.intelligence.product_name

    def test_different_images_produce_different_observations(self) -> None:
        """The seed covers the bytes, not just the name."""
        provider = MockVisionProvider(_settings())
        one = provider.analyze_product(_images(2), product_name="净化器")
        two = provider.analyze_product(_images(5), product_name="净化器")
        assert one.intelligence != two.intelligence

    def test_it_reports_the_prompt_it_used(self) -> None:
        """§15 — key and version on every call, mock included."""
        result = MockVisionProvider(_settings()).analyze_product(_images())
        assert result.prompt_key == "product_analyze_v1"
        assert result.prompt_version >= 1

    def test_it_reports_cost_metadata(self) -> None:
        """§20 requires it, and PHASE 18 bills against it."""
        result = MockVisionProvider(_settings()).analyze_product(_images())
        assert result.usage.input_tokens is not None
        assert result.usage.output_tokens is not None
        assert result.usage.latency_ms is not None

    def test_it_never_invents_legible_text(self) -> None:
        """The one field a mock must leave empty.

        `visible_text` is the strongest evidence a reviewer sees. A mock that
        filled it would be fabricating exactly the field §13 most protects —
        and would teach the review UI to display invented model numbers.
        """
        result = MockVisionProvider(_settings()).analyze_product(_images(), product_name="净化器")
        assert result.intelligence.visible_text == []
        assert "visible_text" in result.intelligence.uncertain_fields


class TestMockFailureInjection:
    """§172: each mode must reach a *different* branch in the caller."""

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("unavailable", ProviderUnavailableError),
            ("rate_limited", ProviderRateLimitedError),
            ("rejected", ProviderRejectedError),
        ],
    )
    def test_transport_failures_raise_their_own_error_type(
        self, mode: str, expected: type[Exception]
    ) -> None:
        provider = MockVisionProvider(_settings(mock_vision_mode=mode))
        with pytest.raises(expected):
            provider.analyze_product(_images())

    def test_malformed_output_is_not_a_provider_error(self) -> None:
        """The distinction that matters for retry policy.

        A 200 whose body fails schema validation means the provider is up and
        answering; retrying the identical request just spends money again.
        Classifying it as `ProviderUnavailableError` would put it in the retry
        path, so it is deliberately a plain `ValueError`.
        """
        provider = MockVisionProvider(_settings(mock_vision_mode="malformed"))
        with pytest.raises(ValueError) as caught:
            provider.analyze_product(_images())
        assert not isinstance(caught.value, ProviderUnavailableError)

    def test_an_empty_result_is_a_success_not_a_failure(self) -> None:
        """A provider that looked and found nothing has still answered."""
        provider = MockVisionProvider(_settings(mock_vision_mode="empty"))
        result = provider.analyze_product(_images())
        assert result.intelligence.is_empty
        assert result.intelligence.uncertain_fields

    def test_no_images_is_rejected_rather_than_analysed(self) -> None:
        with pytest.raises(ProviderRejectedError):
            MockVisionProvider(_settings()).analyze_product([])


class TestObservedInferredBoundary:
    """§14 and §109, at the level where the guarantee is structural."""

    def test_the_two_field_sets_do_not_overlap(self) -> None:
        assert not set(OBSERVED_FIELDS) & set(INFERRED_FIELDS)

    def test_selling_points_are_classified_as_inference(self) -> None:
        """§109's specific prohibition: never usable as factual advertising."""
        assert "possible_selling_points" in INFERRED_FIELDS
        assert "possible_selling_points" not in OBSERVED_FIELDS

    def test_visible_text_is_classified_as_observation(self) -> None:
        assert "visible_text" in OBSERVED_FIELDS

    def test_every_declared_field_exists_on_the_model(self) -> None:
        """Catches a rename that would silently empty one of the sets."""
        fields = set(ProductIntelligence.model_fields)
        assert set(OBSERVED_FIELDS) <= fields
        assert set(INFERRED_FIELDS) <= fields

    def test_observed_and_inferred_items_partition_by_field(self) -> None:
        intelligence = ProductIntelligence(colors=["白"], possible_selling_points=["看起来很高级"])
        assert intelligence.observed_items()["colors"] == ["白"]
        assert intelligence.inferred_items()["possible_selling_points"] == ["看起来很高级"]
        assert "possible_selling_points" not in intelligence.observed_items()

    def test_an_unknown_field_is_rejected_rather_than_dropped(self) -> None:
        """`extra="forbid"` is the drift detector §14 asks for."""
        with pytest.raises(ValueError):
            ProductIntelligence.model_validate({"colors": [], "certified_efficiency": "99.97%"})

    def test_visual_dna_also_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValueError):
            VisualDNA.model_validate({"tone": [], "music": ["upbeat"]})

    def test_is_empty_distinguishes_nothing_found_from_something_found(self) -> None:
        assert ProductIntelligence().is_empty
        assert not ProductIntelligence(colors=["白"]).is_empty
        # Speculation alone is not a finding: a model that guessed at use cases
        # while observing nothing has told us nothing about the product.
        assert ProductIntelligence(possible_use_cases=["客厅"]).is_empty


class TestProviderSelection:
    def test_mock_providers_flag_wins_over_a_configured_vendor(self) -> None:
        """A key in the environment must never cause an accidental spend."""
        provider = get_vision_provider(
            _settings(
                use_mock_providers=True,
                default_vision_provider="anthropic",
                enable_real_vision_provider=True,
                anthropic_api_key="sk-not-real",
            )
        )
        assert provider.name == "mock"

    def test_a_real_provider_without_its_feature_flag_is_refused(self) -> None:
        """§170: the mock path must stay the default, not a silent fallback."""
        with pytest.raises(ProviderUnavailableError):
            get_vision_provider(
                _settings(
                    use_mock_providers=False,
                    default_vision_provider="anthropic",
                    enable_real_vision_provider=False,
                )
            )

    def test_an_unknown_provider_name_is_refused(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            get_vision_provider(
                _settings(
                    use_mock_providers=False,
                    default_vision_provider="nonesuch",
                    enable_real_vision_provider=True,
                )
            )

    def test_the_mock_satisfies_the_vision_provider_protocol(self) -> None:
        """§20's contract, checked structurally rather than by inheritance."""
        assert isinstance(MockVisionProvider(_settings()), VisionProvider)
