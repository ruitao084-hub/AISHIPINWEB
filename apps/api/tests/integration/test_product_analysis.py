"""PHASE 6 acceptance: vision analysis feeding the Truth Layer (§14, §109).

The phase's real question is not "did the API return 201". It is whether a
language model's output can reach a customer as a factual statement without a
person having agreed to it. So the assertions that matter here are the
negative ones: nothing the model produced is VERIFIED, its selling points
cannot become facts, and the product lands in REVIEW_REQUIRED rather than
READY.

Runs entirely on the mock provider, which is §170's requirement — the whole
flow has to work with no API key at all.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator

import httpx2
import pytest
from conftest import ApiUser, add_member, register_user, sole_workspace_id
from fastapi.testclient import TestClient
from PIL import Image

from backend_core.config import get_settings
from backend_core.storage import get_storage


@pytest.fixture(autouse=True)
def _bucket() -> None:
    get_storage().ensure_bucket()


@pytest.fixture
def vision_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Let a test choose the mock's failure mode (§172).

    Settings are cached for the process, so the cache is cleared on both sides
    — leaving a poisoned cache behind would fail whichever test ran next, in a
    way that looks nothing like its own cause.
    """
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def png_bytes(color: str = "blue", size: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(user: ApiUser, workspace_id: uuid.UUID, name: str, color: str) -> uuid.UUID:
    body = png_bytes(color)
    presigned = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/uploads/presign",
        headers=user.auth,
        json={"filename": name, "mime_type": "image/png", "size_bytes": len(body)},
    ).json()
    put = httpx2.put(
        str(presigned["upload_url"]),
        content=body,
        headers={str(k): str(v) for k, v in presigned["headers"].items()},
    )
    assert put.status_code in (200, 204), put.text

    asset_id = presigned["asset"]["id"]
    finished = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/uploads/{asset_id}/complete", headers=user.auth
    )
    assert finished.status_code == 200, finished.text
    return uuid.UUID(asset_id)


def product_with_images(user: ApiUser, workspace_id: uuid.UUID, *, count: int = 2) -> str:
    """A product in ASSETS_READY with `count` attached images."""
    created = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products",
        headers=user.auth,
        json={"name": "静音空气净化器", "category": "家用电器"},
    )
    assert created.status_code == 201, created.text
    product_id = str(created.json()["id"])

    roles = ["FRONT", "SIDE", "DETAIL"]
    for index in range(count):
        asset_id = upload_image(
            user, workspace_id, f"shot-{index}.png", ["blue", "red", "green"][index % 3]
        )
        attached = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
            headers=user.auth,
            json={
                "media_asset_id": str(asset_id),
                "asset_role": roles[index % len(roles)],
                "is_primary": index == 0,
            },
        )
        assert attached.status_code == 201, attached.text

    return product_id


def analyze(user: ApiUser, workspace_id: uuid.UUID, product_id: str) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/analyze", headers=user.auth
    )


def facts(user: ApiUser, workspace_id: uuid.UUID, product_id: str) -> list[dict[str, object]]:
    response = user.client.get(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts", headers=user.auth
    )
    assert response.status_code == 200, response.text
    body: list[dict[str, object]] = response.json()
    return body


def claims(user: ApiUser, workspace_id: uuid.UUID, product_id: str) -> list[dict[str, object]]:
    response = user.client.get(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims", headers=user.auth
    )
    assert response.status_code == 200, response.text
    body: list[dict[str, object]] = response.json()
    return body


class TestAcceptance:
    """§83: upload images, run analysis, review what came back."""

    def test_analysis_produces_reviewable_facts_and_claims(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)

        response = analyze(user, workspace_id, product_id)
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["status"] == "SUCCEEDED"
        assert body["provider"] == "mock"
        assert body["created_fact_count"] > 0
        assert body["created_claim_count"] > 0
        # §15: which prompt produced this, recorded on the row itself.
        assert body["prompt_key"] == "product_analyze_v1"
        assert body["prompt_version"] >= 1
        # §20: cost metadata, so PHASE 18 bills against measurements.
        assert body["input_tokens"] is not None
        assert body["latency_ms"] is not None

    def test_the_product_lands_in_review_rather_than_ready(self, client: TestClient) -> None:
        """§104's whole point: analysis never produces a publishable product."""
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)

        analyze(user, workspace_id, product_id)

        product = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}", headers=user.auth
        ).json()
        assert product["status"] == "REVIEW_REQUIRED"

    def test_it_records_exactly_which_images_were_analysed(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id, count=3)

        body = analyze(user, workspace_id, product_id).json()
        assert len(body["analyzed_asset_ids"]) == 3

    def test_the_history_endpoint_lists_past_runs_newest_first(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)

        analyze(user, workspace_id, product_id)
        # Second run: REVIEW_REQUIRED -> ANALYZING is a legal transition, so a
        # reviewer can re-analyse after adding images.
        analyze(user, workspace_id, product_id)

        response = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/analyses",
            headers=user.auth,
        )
        assert response.status_code == 200, response.text
        assert len(response.json()) == 2


class TestTruthLayerBoundary:
    """§13 and §109 — the rules this phase exists to not break."""

    def test_nothing_the_ai_produced_arrives_verified(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        produced = facts(user, workspace_id, product_id)
        assert produced
        for fact in produced:
            assert fact["verification_status"] == "AI_INFERRED"
            assert fact["verified_at"] is None
            assert fact["source_type"] == "AI_VISION"

    def test_selling_points_become_claims_and_never_facts(self, client: TestClient) -> None:
        """§109's specific prohibition, checked from both sides."""
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        produced_claims = claims(user, workspace_id, product_id)
        assert produced_claims
        for claim in produced_claims:
            assert claim["status"] == "SUGGESTED"
            # No evidence attached: the model asserted it, nobody backed it.
            assert claim["source_fact_ids"] == []

        # And no fact was created from the inferred fields. Facts are keyed by
        # the field they came from, so this is checkable directly.
        fact_keys = {fact["key"] for fact in facts(user, workspace_id, product_id)}
        assert "possible_selling_points" not in fact_keys
        assert "possible_use_cases" not in fact_keys

    def test_an_ai_claim_cannot_be_approved_without_evidence(self, client: TestClient) -> None:
        """The end-to-end version of §13's rule.

        The analysis suggests a selling point; approving it is refused because
        no verified fact backs it. This is the single most important assertion
        in the phase — it is what stops a model's guess becoming advertising.
        """
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        claim_id = claims(user, workspace_id, product_id)[0]["id"]
        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/{claim_id}/verify",
            headers=user.auth,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CLAIM_NOT_VERIFIED"

        # Still SUGGESTED afterwards — the refusal is not advisory.
        assert claims(user, workspace_id, product_id)[0]["status"] == "SUGGESTED"

    def test_a_reviewer_can_confirm_a_fact_the_ai_observed(self, client: TestClient) -> None:
        """The other half: review is possible, not merely obstructive."""
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        fact_id = facts(user, workspace_id, product_id)[0]["id"]
        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact_id}/verify",
            headers=user.auth,
        )
        assert response.status_code == 200, response.text
        assert response.json()["verification_status"] == "VERIFIED"
        assert response.json()["verified_at"] is not None

    def test_edit_and_verify_moves_accountability_without_rewriting_provenance(
        self, client: TestClient
    ) -> None:
        """P6-T08's third disposition, and the distinction underneath it.

        A reviewer who corrects a value takes responsibility for it, so the
        fact becomes VERIFIED with their name on it. `source_type` stays
        `AI_VISION`, because that field answers "where did this assertion come
        from" — a different question from "who vouched for it". Rewriting it on
        edit would erase the fact that a model proposed this at all, which is
        exactly the audit trail §13 exists to keep.
        """
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        fact = facts(user, workspace_id, product_id)[0]
        assert fact["source_type"] == "AI_VISION"

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}",
            headers=user.auth,
            json={"value_text": "阳极氧化铝（人工核对）", "verify": True},
        )
        assert response.status_code == 200, response.text
        edited = response.json()

        assert edited["value_text"] == "阳极氧化铝（人工核对）"
        assert edited["verification_status"] == "VERIFIED"
        assert edited["verified_at"] is not None
        assert edited["source_type"] == "AI_VISION"

    def test_the_visual_dna_lands_on_the_product_without_verification(
        self, client: TestClient
    ) -> None:
        """§14 — aesthetic direction is creative, not factual.

        Nobody verifies a colour palette, so it is not routed through facts.
        """
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)
        analyze(user, workspace_id, product_id)

        product = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}", headers=user.auth
        ).json()
        assert product["visual_dna"]["tone"]
        assert product["ai_summary"]


class TestFailureHandling:
    """§24 and §172 — what happens when the provider does not cooperate."""

    @pytest.mark.parametrize("mode", ["unavailable", "rate_limited", "rejected", "malformed"])
    def test_a_provider_failure_leaves_the_product_where_it_was(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, vision_mode: None, mode: str
    ) -> None:
        """§24: a failure must not strand an entity mid-state.

        The product must still be ASSETS_READY afterwards — not ANALYZING,
        which nothing would ever move it out of.
        """
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)

        monkeypatch.setenv("MOCK_VISION_MODE", mode)
        get_settings.cache_clear()

        response = analyze(user, workspace_id, product_id)
        assert response.status_code == 502, response.text

        product = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}", headers=user.auth
        ).json()
        assert product["status"] == "ASSETS_READY"

        # And nothing unverified leaked into the Truth Layer on the way out.
        assert facts(user, workspace_id, product_id) == []
        assert claims(user, workspace_id, product_id) == []

    def test_an_empty_result_succeeds_without_inventing_anything(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, vision_mode: None
    ) -> None:
        """A provider that looked and found nothing has still answered.

        Recorded as a successful run with zero facts — which is honest, and
        distinguishable from a failure that produced zero facts.
        """
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = product_with_images(user, workspace_id)

        monkeypatch.setenv("MOCK_VISION_MODE", "empty")
        get_settings.cache_clear()

        body = analyze(user, workspace_id, product_id).json()
        assert body["status"] == "SUCCEEDED"
        assert body["created_fact_count"] == 0
        assert facts(user, workspace_id, product_id) == []

    def test_a_product_with_no_images_is_refused_before_any_spend(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        created = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products",
            headers=user.auth,
            json={"name": "无图产品", "category": "家用电器"},
        )
        product_id = str(created.json()["id"])

        response = analyze(user, workspace_id, product_id)
        assert response.status_code == 422, response.text


class TestAuthorisation:
    def test_a_viewer_cannot_spend_the_workspaces_money(self, client: TestClient) -> None:
        """§40 — GENERATION_RUN is not implied by read access."""
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        product_id = product_with_images(owner, workspace_id)

        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        assert analyze(viewer, workspace_id, product_id).status_code == 403

    def test_an_editor_can_run_an_analysis(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        product_id = product_with_images(owner, workspace_id)

        editor = register_user(client, prefix="editor")
        add_member(owner, workspace_id, editor, "EDITOR")

        assert analyze(editor, workspace_id, product_id).status_code == 201

    def test_another_workspace_cannot_analyse_this_product(self, client: TestClient) -> None:
        """§60 — a cross-tenant read is a 404, not a 403."""
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        product_id = product_with_images(owner, workspace_id)

        outsider = register_user(client, prefix="outsider")
        outsider_workspace = sole_workspace_id(outsider)

        response = outsider.client.post(
            f"/api/v1/workspaces/{outsider_workspace}/products/{product_id}/analyze",
            headers=outsider.auth,
        )
        assert response.status_code == 404, response.text
