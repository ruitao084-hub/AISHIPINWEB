"""Product state machine and claim-risk rules (§104, §13).

Pure domain logic, no database. The transition table and the "which claims
need evidence" rule are policy, and policy that only exists inside a service
method is policy nobody can see.
"""

from __future__ import annotations

import pytest

from backend_core.domain.enums import (
    FACT_BACKED_CLAIM_TYPES,
    ClaimRiskLevel,
    ClaimStatus,
    ClaimType,
    ProductStatus,
    VerificationStatus,
    allowed_product_transitions,
    can_transition_product,
    default_risk_level,
    requires_fact_backing,
)


class TestProductTransitions:
    def test_a_new_product_can_only_gain_assets_or_be_archived(self) -> None:
        assert allowed_product_transitions(ProductStatus.DRAFT) == frozenset(
            {ProductStatus.ASSETS_READY, ProductStatus.ARCHIVED}
        )

    def test_a_product_cannot_jump_straight_from_draft_to_ready(self) -> None:
        """The path §13 depends on: nothing reaches READY without review."""
        assert not can_transition_product(ProductStatus.DRAFT, ProductStatus.READY)

    def test_a_product_cannot_skip_review_after_analysis(self) -> None:
        assert not can_transition_product(ProductStatus.ANALYZING, ProductStatus.READY)
        assert can_transition_product(ProductStatus.ANALYZING, ProductStatus.REVIEW_REQUIRED)
        assert can_transition_product(ProductStatus.REVIEW_REQUIRED, ProductStatus.READY)

    def test_a_hand_entered_product_may_skip_analysis(self) -> None:
        """§13 treats user-supplied data as the strongest source, so a product
        whose facts were all typed in never needs the analyser."""
        assert can_transition_product(ProductStatus.ASSETS_READY, ProductStatus.READY)

    def test_failed_analysis_returns_the_product_rather_than_stranding_it(self) -> None:
        """§24 — a failure must not leave an entity in a transient state."""
        assert can_transition_product(ProductStatus.ANALYZING, ProductStatus.ASSETS_READY)

    def test_losing_the_last_image_returns_a_product_to_draft(self) -> None:
        assert can_transition_product(ProductStatus.ASSETS_READY, ProductStatus.DRAFT)

    def test_a_ready_product_can_be_sent_back_for_review(self) -> None:
        """Editing a verified fact invalidates claims built on it."""
        assert can_transition_product(ProductStatus.READY, ProductStatus.REVIEW_REQUIRED)

    def test_archive_is_reachable_from_every_live_state(self) -> None:
        for status in ProductStatus:
            if status is ProductStatus.ARCHIVED:
                continue
            assert can_transition_product(status, ProductStatus.ARCHIVED), status

    def test_archive_is_terminal(self) -> None:
        assert allowed_product_transitions(ProductStatus.ARCHIVED) == frozenset()
        for status in ProductStatus:
            if status is ProductStatus.ARCHIVED:
                continue
            assert not can_transition_product(ProductStatus.ARCHIVED, status), status

    @pytest.mark.parametrize("status", list(ProductStatus))
    def test_staying_put_is_always_allowed(self, status: ProductStatus) -> None:
        """A no-op write must not raise; only *changes* are policed."""
        assert can_transition_product(status, status)

    @pytest.mark.parametrize("status", list(ProductStatus))
    def test_every_status_has_a_transition_entry(self, status: ProductStatus) -> None:
        """A status missing from the table would raise KeyError at runtime."""
        assert isinstance(allowed_product_transitions(status), frozenset)


class TestClaimEvidenceRules:
    @pytest.mark.parametrize(
        "claim_type",
        [
            ClaimType.PERFORMANCE,
            ClaimType.COMPARATIVE,
            ClaimType.SAFETY,
            ClaimType.CERTIFICATION,
            ClaimType.FUNCTIONAL,
        ],
    )
    def test_anything_checkable_needs_a_verified_fact(self, claim_type: ClaimType) -> None:
        """§13's example: "helps filter impurities" is allowed *only if* that
        function has been confirmed as a fact."""
        assert requires_fact_backing(claim_type)

    def test_only_emotional_claims_are_exempt(self) -> None:
        """ "Brings calm to your morning" asserts nothing checkable, so
        demanding evidence for it would be theatre."""
        assert not requires_fact_backing(ClaimType.EMOTIONAL)
        exempt = set(ClaimType) - FACT_BACKED_CLAIM_TYPES
        assert exempt == {ClaimType.EMOTIONAL}

    @pytest.mark.parametrize(
        ("claim_type", "expected"),
        [
            (ClaimType.PERFORMANCE, ClaimRiskLevel.HIGH),
            (ClaimType.SAFETY, ClaimRiskLevel.HIGH),
            (ClaimType.COMPARATIVE, ClaimRiskLevel.HIGH),
            (ClaimType.CERTIFICATION, ClaimRiskLevel.HIGH),
            (ClaimType.FUNCTIONAL, ClaimRiskLevel.MEDIUM),
            (ClaimType.EMOTIONAL, ClaimRiskLevel.LOW),
        ],
    )
    def test_quantified_and_safety_claims_default_to_high_risk(
        self, claim_type: ClaimType, expected: ClaimRiskLevel
    ) -> None:
        assert default_risk_level(claim_type) is expected

    @pytest.mark.parametrize("claim_type", list(ClaimType))
    def test_every_claim_type_has_a_default_risk(self, claim_type: ClaimType) -> None:
        assert isinstance(default_risk_level(claim_type), ClaimRiskLevel)


class TestTruthVocabulary:
    def test_the_four_verification_states_match_the_taskbook(self) -> None:
        assert {status.value for status in VerificationStatus} == {
            "AI_INFERRED",
            "USER_PROVIDED",
            "VERIFIED",
            "REJECTED",
        }

    def test_the_three_claim_states_match_the_taskbook(self) -> None:
        assert {status.value for status in ClaimStatus} == {
            "SUGGESTED",
            "VERIFIED",
            "REJECTED",
        }

    def test_ai_inferred_is_not_verified(self) -> None:
        """Stated as a test because the whole layer rests on it."""
        assert VerificationStatus.AI_INFERRED is not VerificationStatus.VERIFIED
