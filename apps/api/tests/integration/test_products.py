"""PHASE 5 acceptance: products and the Product Truth Layer (§82, §13, §109).

The §82 criteria — create a product, upload several images, set a primary one,
edit facts, confirm claims — are the first class below. Everything after it
tests the rule the phase actually exists for: that the platform cannot end up
stating something no human confirmed.
"""

from __future__ import annotations

import io
import uuid

import httpx2
import pytest
from conftest import ApiUser, add_member, register_user, sole_workspace_id
from fastapi.testclient import TestClient
from PIL import Image

from backend_core.storage import get_storage


@pytest.fixture(autouse=True)
def _bucket() -> None:
    get_storage().ensure_bucket()


def png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(user: ApiUser, workspace_id: uuid.UUID, name: str = "shot.png") -> uuid.UUID:
    """Run a real upload and return the READY asset's id."""
    body = png_bytes()
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


def create_product(
    user: ApiUser, workspace_id: uuid.UUID, name: str = "Air Purifier X1", **extra: object
) -> dict[str, object]:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products",
        headers=user.auth,
        json={"name": name, "category": "Home appliance", **extra},
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def create_fact(
    user: ApiUser,
    workspace_id: uuid.UUID,
    product_id: str,
    *,
    key: str = "filtration",
    value_text: str = "HEPA H13 filter",
    fact_type: str = "FEATURE",
    verify: bool = False,
) -> dict[str, object]:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts",
        headers=user.auth,
        json={
            "fact_type": fact_type,
            "key": key,
            "value_text": value_text,
            "verify": verify,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def create_claim(
    user: ApiUser,
    workspace_id: uuid.UUID,
    product_id: str,
    *,
    claim_text: str,
    claim_type: str,
    source_fact_ids: list[str] | None = None,
) -> dict[str, object]:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims",
        headers=user.auth,
        json={
            "claim_text": claim_text,
            "claim_type": claim_type,
            "source_fact_ids": source_fact_ids or [],
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def verify_claim(
    user: ApiUser, workspace_id: uuid.UUID, product_id: str, claim_id: str
) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/{claim_id}/verify",
        headers=user.auth,
    )


class TestAcceptance:
    """§82: create a product, upload images, set a primary, edit facts, confirm claims."""

    def test_the_whole_product_flow(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)

        product = create_product(user, workspace_id, sku="APX1")
        product_id = str(product["id"])
        assert product["status"] == "DRAFT"

        # Several images, as §82 requires.
        first = upload_image(user, workspace_id, "front.png")
        second = upload_image(user, workspace_id, "side.png")
        for asset_id, role in ((first, "FRONT"), (second, "SIDE")):
            attached = user.client.post(
                f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
                headers=user.auth,
                json={"media_asset_id": str(asset_id), "asset_role": role},
            )
            assert attached.status_code == 201, attached.text

        # Attaching imagery moves the product on (§104).
        detail = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}", headers=user.auth
        )
        assert detail.json()["status"] == "ASSETS_READY"

        links = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets", headers=user.auth
        ).json()
        assert [link["asset_role"] for link in links] == ["FRONT", "SIDE"]
        # The first image attached becomes primary without being asked.
        assert links[0]["is_primary"] is True

        # Set the *second* image as primary.
        promoted = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}"
            f"/assets/{links[1]['id']}/primary",
            headers=user.auth,
        )
        assert promoted.status_code == 200
        reordered = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets", headers=user.auth
        ).json()
        assert [link["is_primary"] for link in reordered] == [True, False]
        assert reordered[0]["id"] == links[1]["id"]

        # Facts: create, edit, confirm.
        fact = create_fact(user, workspace_id, product_id)
        assert fact["verification_status"] == "USER_PROVIDED"

        edited = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}",
            headers=user.auth,
            json={"value_text": "HEPA H13 filter, replaceable"},
        )
        assert edited.status_code == 200
        assert edited.json()["value_text"] == "HEPA H13 filter, replaceable"

        confirmed = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}/verify",
            headers=user.auth,
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["verification_status"] == "VERIFIED"
        assert confirmed.json()["verified_at"] is not None

        # Claims: propose, then confirm against the verified fact.
        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Helps filter impurities and odours from the air.",
            claim_type="FUNCTIONAL",
            source_fact_ids=[str(fact["id"])],
        )
        assert claim["status"] == "SUGGESTED"

        approved = verify_claim(user, workspace_id, product_id, str(claim["id"]))
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "VERIFIED"

        ready = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/ready", headers=user.auth
        )
        assert ready.status_code == 200
        assert ready.json()["status"] == "READY"


class TestTruthLayer:
    """§13 — the platform must not state what nobody confirmed."""

    def test_a_performance_claim_with_no_evidence_is_refused(self, client: TestClient) -> None:
        """§13's forbidden example, as a test.

        "Removes 99.9% of formaldehyde" is exactly the sentence the layer
        exists to stop, and it is stopped for lacking evidence, not for
        containing a number.
        """
        user = register_user(client, prefix="claims1")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Removes 99.9% of formaldehyde.",
            claim_type="PERFORMANCE",
        )
        assert claim["risk_level"] == "HIGH"

        refused = verify_claim(user, workspace_id, product_id, str(claim["id"]))
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "CLAIM_NOT_VERIFIED"

    def test_a_claim_citing_an_unverified_fact_is_refused(self, client: TestClient) -> None:
        """Citing evidence is not the same as having evidence."""
        user = register_user(client, prefix="claims2")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        fact = create_fact(user, workspace_id, product_id, verify=False)
        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Uses a HEPA filter.",
            claim_type="FUNCTIONAL",
            source_fact_ids=[str(fact["id"])],
        )

        refused = verify_claim(user, workspace_id, product_id, str(claim["id"]))
        assert refused.status_code == 409
        assert refused.json()["error"]["details"]["verified_facts"] == 0

    def test_an_emotional_claim_needs_no_evidence(self, client: TestClient) -> None:
        """It asserts nothing checkable, so there is nothing to substantiate."""
        user = register_user(client, prefix="claims3")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Brings a little calm to your morning.",
            claim_type="EMOTIONAL",
        )
        assert claim["risk_level"] == "LOW"

        approved = verify_claim(user, workspace_id, product_id, str(claim["id"]))
        assert approved.status_code == 200
        assert approved.json()["status"] == "VERIFIED"

    def test_rejecting_a_fact_withdraws_the_claims_built_on_it(self, client: TestClient) -> None:
        """The quiet failure mode this layer would otherwise have.

        Verify a fact, verify a claim citing it, then reject the fact. Without
        the cascade the claim stays VERIFIED — a script would go on quoting
        evidence that has been withdrawn, which is the fabricated statement
        §13 forbids, arrived at one legitimate step at a time.
        """
        user = register_user(client, prefix="cascade")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        fact = create_fact(user, workspace_id, product_id, verify=True)
        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Uses a HEPA H13 filter.",
            claim_type="FUNCTIONAL",
            source_fact_ids=[str(fact["id"])],
        )
        assert verify_claim(user, workspace_id, product_id, str(claim["id"])).status_code == 200

        rejected = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}/reject",
            headers=user.auth,
        )
        assert rejected.status_code == 200
        assert rejected.json()["verification_status"] == "REJECTED"

        after = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims", headers=user.auth
        ).json()
        assert after[0]["status"] == "SUGGESTED"
        assert after[0]["verified_at"] is None

        usable = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/verified",
            headers=user.auth,
        ).json()
        assert usable == []

    def test_editing_a_verified_fact_withdraws_its_verification(self, client: TestClient) -> None:
        """ "Removes 99.9%" becoming "removes 50%" makes every claim citing it
        wrong, so the edit cannot silently keep the old approval."""
        user = register_user(client, prefix="edit")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        fact = create_fact(user, workspace_id, product_id, verify=True)
        claim = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Uses a HEPA H13 filter.",
            claim_type="FUNCTIONAL",
            source_fact_ids=[str(fact["id"])],
        )
        assert verify_claim(user, workspace_id, product_id, str(claim["id"])).status_code == 200

        edited = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}",
            headers=user.auth,
            json={"value_text": "Basic mesh filter"},
        )
        assert edited.status_code == 200
        assert edited.json()["verification_status"] == "USER_PROVIDED"

        claims = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims", headers=user.auth
        ).json()
        assert claims[0]["status"] == "SUGGESTED"

    def test_editing_only_the_key_leaves_verification_intact(self, client: TestClient) -> None:
        """Renaming a field is bookkeeping; the assertion has not changed."""
        user = register_user(client, prefix="rename")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        fact = create_fact(user, workspace_id, product_id, verify=True)

        edited = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}",
            headers=user.auth,
            json={"key": "filter_type"},
        )
        assert edited.status_code == 200
        assert edited.json()["verification_status"] == "VERIFIED"

    def test_edit_and_reverify_in_one_step(self, client: TestClient) -> None:
        """The "Edit + Verify" action the review UI needs (§83 P6-T08)."""
        user = register_user(client, prefix="editverify")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        fact = create_fact(user, workspace_id, product_id, verify=True)

        edited = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}",
            headers=user.auth,
            json={"value_text": "HEPA H14 filter", "verify": True},
        )
        assert edited.status_code == 200
        assert edited.json()["verification_status"] == "VERIFIED"

    def test_a_claim_cannot_cite_another_products_facts(self, client: TestClient) -> None:
        """Otherwise a claim points at someone else's verified fact and looks
        substantiated."""
        user = register_user(client, prefix="crossref")
        workspace_id = sole_workspace_id(user)
        first = str(create_product(user, workspace_id, name="Purifier")["id"])
        second = str(create_product(user, workspace_id, name="Humidifier")["id"])

        foreign_fact = create_fact(user, workspace_id, first, verify=True)

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{second}/claims",
            headers=user.auth,
            json={
                "claim_text": "Uses a HEPA filter.",
                "claim_type": "FUNCTIONAL",
                "source_fact_ids": [str(foreign_fact["id"])],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_verified_claims_endpoint_returns_only_approved_claims(
        self, client: TestClient
    ) -> None:
        """§109's accessor. This is what the PHASE 7 script generator calls."""
        user = register_user(client, prefix="verified")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        fact = create_fact(user, workspace_id, product_id, verify=True)

        approved = create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Uses a HEPA H13 filter.",
            claim_type="FUNCTIONAL",
            source_fact_ids=[str(fact["id"])],
        )
        create_claim(
            user,
            workspace_id,
            product_id,
            claim_text="Removes 99.9% of formaldehyde.",
            claim_type="PERFORMANCE",
        )
        assert verify_claim(user, workspace_id, product_id, str(approved["id"])).status_code == 200

        usable = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/verified",
            headers=user.auth,
        ).json()
        assert [claim["claim_text"] for claim in usable] == ["Uses a HEPA H13 filter."]

    def test_a_product_with_no_verified_facts_cannot_be_marked_ready(
        self, client: TestClient
    ) -> None:
        """§13's "not enough verified claims" case: nothing to say truthfully."""
        user = register_user(client, prefix="notready")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        create_fact(user, workspace_id, product_id, verify=False)

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/ready", headers=user.auth
        )
        assert response.status_code == 422


class TestProductLifecycle:
    def test_status_cannot_be_written_through_the_edit_endpoint(self, client: TestClient) -> None:
        """§105 — status changes go through their own transitions."""
        user = register_user(client, prefix="nostatus")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}",
            headers=user.auth,
            json={"name": "Renamed", "status": "READY"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "DRAFT"

    def test_detaching_the_last_image_returns_the_product_to_draft(
        self, client: TestClient
    ) -> None:
        user = register_user(client, prefix="detach")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        asset_id = upload_image(user, workspace_id)

        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
            headers=user.auth,
            json={"media_asset_id": str(asset_id)},
        )
        links = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets", headers=user.auth
        ).json()

        removed = user.client.delete(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets/{links[0]['id']}",
            headers=user.auth,
        )
        assert removed.status_code == 204

        detail = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}", headers=user.auth
        ).json()
        assert detail["status"] == "DRAFT"

    def test_detaching_the_primary_promotes_the_next_image(self, client: TestClient) -> None:
        """A product with images must always have a hero shot."""
        user = register_user(client, prefix="reprimary")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        for name in ("a.png", "b.png"):
            asset_id = upload_image(user, workspace_id, name)
            user.client.post(
                f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
                headers=user.auth,
                json={"media_asset_id": str(asset_id)},
            )

        links = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets", headers=user.auth
        ).json()
        primary_id = next(link["id"] for link in links if link["is_primary"])

        user.client.delete(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets/{primary_id}",
            headers=user.auth,
        )

        remaining = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets", headers=user.auth
        ).json()
        assert len(remaining) == 1
        assert remaining[0]["is_primary"] is True

    def test_an_unfinished_upload_cannot_be_attached(self, client: TestClient) -> None:
        """§12 — a PENDING asset has no confirmed bytes behind it."""
        user = register_user(client, prefix="pendingattach")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])

        presigned = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/uploads/presign",
            headers=user.auth,
            json={"filename": "x.png", "mime_type": "image/png", "size_bytes": 1024},
        ).json()

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
            headers=user.auth,
            json={"media_asset_id": presigned["asset"]["id"]},
        )
        assert response.status_code == 422

    def test_a_duplicate_sku_is_refused(self, client: TestClient) -> None:
        user = register_user(client, prefix="sku")
        workspace_id = sole_workspace_id(user)
        create_product(user, workspace_id, name="First", sku="SAME")

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products",
            headers=user.auth,
            json={"name": "Second", "category": "Home appliance", "sku": "SAME"},
        )
        assert response.status_code == 409

    def test_products_without_a_sku_do_not_collide(self, client: TestClient) -> None:
        """The uniqueness index is partial; NULL is not a value."""
        user = register_user(client, prefix="nosku")
        workspace_id = sole_workspace_id(user)
        create_product(user, workspace_id, name="First")
        create_product(user, workspace_id, name="Second")

    def test_an_archived_product_cannot_be_revived_by_a_transition(
        self, client: TestClient
    ) -> None:
        """§104 makes ARCHIVED terminal; restoring is a separate decision."""
        user = register_user(client, prefix="archived")
        workspace_id = sole_workspace_id(user)
        product_id = str(create_product(user, workspace_id)["id"])
        create_fact(user, workspace_id, product_id, verify=True)

        assert (
            user.client.post(
                f"/api/v1/workspaces/{workspace_id}/products/{product_id}/archive",
                headers=user.auth,
            ).status_code
            == 200
        )

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/ready", headers=user.auth
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROJECT_INVALID_STATE"


class TestIsolationAndPermissions:
    def test_products_are_invisible_across_workspaces(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        owner_workspace = sole_workspace_id(owner)
        product_id = str(create_product(owner, owner_workspace)["id"])

        stranger = register_user(client, prefix="stranger")
        stranger_workspace = sole_workspace_id(stranger)

        # Right id, wrong workspace, and a caller with no membership: 404 in
        # both directions, never a 403 that would confirm the product exists.
        assert (
            stranger.client.get(
                f"/api/v1/workspaces/{owner_workspace}/products/{product_id}",
                headers=stranger.auth,
            ).status_code
            == 404
        )
        assert (
            stranger.client.get(
                f"/api/v1/workspaces/{stranger_workspace}/products/{product_id}",
                headers=stranger.auth,
            ).status_code
            == 404
        )

    def test_a_viewer_can_read_but_not_verify(self, client: TestClient) -> None:
        """Verification is taking responsibility, so it needs write access."""
        owner = register_user(client, prefix="wsowner")
        workspace_id = sole_workspace_id(owner)
        product_id = str(create_product(owner, workspace_id)["id"])
        fact = create_fact(owner, workspace_id, product_id)

        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        assert (
            viewer.client.get(
                f"/api/v1/workspaces/{workspace_id}/products", headers=viewer.auth
            ).status_code
            == 200
        )

        denied = viewer.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}/verify",
            headers=viewer.auth,
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "WORKSPACE_FORBIDDEN"

    def test_an_editor_can_verify(self, client: TestClient) -> None:
        owner = register_user(client, prefix="wsowner2")
        workspace_id = sole_workspace_id(owner)
        product_id = str(create_product(owner, workspace_id)["id"])
        fact = create_fact(owner, workspace_id, product_id)

        editor = register_user(client, prefix="editor")
        add_member(owner, workspace_id, editor, "EDITOR")

        approved = editor.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts/{fact['id']}/verify",
            headers=editor.auth,
        )
        assert approved.status_code == 200
        assert approved.json()["verification_status"] == "VERIFIED"

    def test_only_admins_can_archive(self, client: TestClient) -> None:
        """Archiving is `product:delete`, which EDITOR does not hold (§40)."""
        owner = register_user(client, prefix="wsowner3")
        workspace_id = sole_workspace_id(owner)
        product_id = str(create_product(owner, workspace_id)["id"])

        editor = register_user(client, prefix="editor2")
        add_member(owner, workspace_id, editor, "EDITOR")

        denied = editor.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/archive",
            headers=editor.auth,
        )
        assert denied.status_code == 403

    def test_a_product_cannot_borrow_another_workspaces_media(self, client: TestClient) -> None:
        """§61 — the asset lookup is workspace-scoped, so a foreign id is a 404."""
        owner = register_user(client, prefix="media1")
        owner_workspace = sole_workspace_id(owner)
        foreign_asset = upload_image(owner, owner_workspace)

        other = register_user(client, prefix="media2")
        other_workspace = sole_workspace_id(other)
        product_id = str(create_product(other, other_workspace)["id"])

        response = other.client.post(
            f"/api/v1/workspaces/{other_workspace}/products/{product_id}/assets",
            headers=other.auth,
            json={"media_asset_id": str(foreign_asset)},
        )
        assert response.status_code == 404
