"""PHASE 8 acceptance: storyboard, shots and compiled prompts (§85, §18, §19, §29).

§85's criteria for a 30-second project — sensible shots, correct total, every
shot carrying a prompt — are the first class.

The two classes after it test the rules the phase exists for. §19 forbids
handing a video model a sentence a user typed, so the strongest assertion here
is that **the API offers no way to write a prompt directly**: a PATCH carrying
`visual_prompt` is rejected, and editing the lighting rebuilds it instead.
§29's identity lock is checked the same way — through the API, on real rows,
rather than by trusting the compiler's unit tests.
"""

from __future__ import annotations

import io
import uuid

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
def llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    yield  # type: ignore[misc]
    monkeypatch.undo()
    get_settings.cache_clear()


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


def upload_image(user: ApiUser, workspace_id: uuid.UUID, name: str) -> uuid.UUID:
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
    done = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/uploads/{asset_id}/complete", headers=user.auth
    )
    assert done.status_code == 200, done.text
    return uuid.UUID(asset_id)


def ready_project(
    client: TestClient, *, prefix: str = "pm", duration: int = 30
) -> tuple[ApiUser, uuid.UUID, str]:
    """A project with an approved script, ready to storyboard."""
    user = register_user(client, prefix=prefix)
    workspace_id = sole_workspace_id(user)

    product = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products",
        headers=user.auth,
        json={"name": "静音空气净化器", "category": "家用电器"},
    ).json()
    product_id = str(product["id"])

    # An image, so §29's identity references have something to point at.
    asset_id = upload_image(user, workspace_id, "front.png")
    attached = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/assets",
        headers=user.auth,
        json={"media_asset_id": str(asset_id), "asset_role": "FRONT", "is_primary": True},
    )
    assert attached.status_code == 201, attached.text

    fact = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts",
        headers=user.auth,
        json={
            "fact_type": "MATERIAL",
            "key": "materials",
            "value_text": "阳极氧化铝",
            "verify": True,
        },
    )
    assert fact.status_code == 201, fact.text

    project = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        headers=user.auth,
        json={
            "product_id": product_id,
            "name": "净化器短片",
            "duration_seconds": duration,
            "target_platform": "DOUYIN",
        },
    ).json()
    project_id = str(project["id"])

    plans = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/creative-plans",
        headers=user.auth,
    ).json()
    user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
        f"/creative-plans/{plans[0]['id']}/select",
        headers=user.auth,
    )
    script = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts", headers=user.auth
    ).json()
    approved = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts/{script['id']}/approve",
        headers=user.auth,
    )
    assert approved.status_code == 200, approved.text

    return user, workspace_id, project_id


def generate_storyboard(user: ApiUser, workspace_id: uuid.UUID, project_id: str) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/storyboards",
        headers=user.auth,
    )


def shots(
    user: ApiUser, workspace_id: uuid.UUID, project_id: str, storyboard_id: str
) -> list[dict[str, object]]:
    response = user.client.get(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
        f"/storyboards/{storyboard_id}/shots",
        headers=user.auth,
    )
    assert response.status_code == 200, response.text
    body: list[dict[str, object]] = response.json()
    return body


class TestAcceptance:
    """§85: a 30-second project gets sensible shots, a correct total, and a
    prompt on every one."""

    def test_the_whole_flow(self, client: TestClient) -> None:
        user, workspace_id, project_id = ready_project(client)

        response = generate_storyboard(user, workspace_id, project_id)
        assert response.status_code == 201, response.text
        storyboard = response.json()

        assert storyboard["version"] == 1
        assert storyboard["status"] == "DRAFT"
        # §18's constraint, at the tolerance the validator enforces.
        assert abs(storyboard["total_duration_seconds"] - 30) <= 3

        produced = shots(user, workspace_id, project_id, str(storyboard["id"]))
        assert len(produced) >= 3
        assert [shot["sequence_no"] for shot in produced] == list(range(1, len(produced) + 1))
        # §85: every shot has a prompt.
        assert all(shot["visual_prompt"] for shot in produced)
        assert all(shot["negative_prompt"] for shot in produced)

    def test_the_shot_durations_sum_to_the_stored_total(self, client: TestClient) -> None:
        """The denormalised total must equal the shots it summarises."""
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        produced = shots(user, workspace_id, project_id, str(storyboard["id"]))

        summed = round(sum(float(shot["duration_seconds"]) for shot in produced), 2)
        assert summed == pytest.approx(storyboard["total_duration_seconds"], abs=0.05)

    @pytest.mark.parametrize("duration", [15, 30, 60])
    def test_the_total_tracks_the_project_duration(self, client: TestClient, duration: int) -> None:
        user, workspace_id, project_id = ready_project(client, duration=duration)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        drift = abs(storyboard["total_duration_seconds"] - duration) / duration
        assert drift <= 0.10, storyboard["total_duration_seconds"]

    def test_a_project_without_an_approved_script_is_refused(self, client: TestClient) -> None:
        """§17's approval is the act by which a person accepted the words;
        storyboarding a draft would make it cosmetic."""
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products",
            headers=user.auth,
            json={"name": "净化器", "category": "家用电器"},
        ).json()
        project = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            headers=user.auth,
            json={"product_id": str(product["id"]), "name": "x", "duration_seconds": 30},
        ).json()

        response = generate_storyboard(user, workspace_id, str(project["id"]))
        assert response.status_code == 422, response.text


class TestPromptCompilation:
    """§19 — never hand a video model a sentence a user typed."""

    def test_the_api_offers_no_way_to_write_a_prompt_directly(self, client: TestClient) -> None:
        """The enforcement, stated as a test.

        `visual_prompt` is not a field of the update request, so a client
        sending one is rejected outright rather than having it silently
        ignored — which would be worse, because the caller would believe it
        worked.
        """
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        shot = shots(user, workspace_id, project_id, str(storyboard["id"]))[0]

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/storyboards/{storyboard['id']}/shots/{shot['id']}",
            headers=user.auth,
            json={"visual_prompt": "just make it look amazing"},
        )
        assert response.status_code == 422, response.text

    def test_editing_a_field_recompiles_the_prompt(self, client: TestClient) -> None:
        """A user changes the lighting; the prompt changes because of it."""
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        shot = shots(user, workspace_id, project_id, str(storyboard["id"]))[0]

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/storyboards/{storyboard['id']}/shots/{shot['id']}",
            headers=user.auth,
            json={"lighting": "harsh overhead studio light"},
        )
        assert response.status_code == 200, response.text
        updated = response.json()

        assert "harsh overhead studio light" in updated["visual_prompt"]
        assert updated["visual_prompt"] != shot["visual_prompt"]

    def test_every_prompt_is_assembled_from_labelled_blocks(self, client: TestClient) -> None:
        """§19's structure, on real rows rather than in a unit test."""
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()

        for shot in shots(user, workspace_id, project_id, str(storyboard["id"])):
            prompt = str(shot["visual_prompt"])
            assert prompt.startswith("SUBJECT:")
            assert "PRODUCT IDENTITY:" in prompt

    def test_the_prompt_describes_the_product_from_verified_facts(self, client: TestClient) -> None:
        """§13 reaches the prompt. Nobody proofreads one before it is sent."""
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        first = shots(user, workspace_id, project_id, str(storyboard["id"]))[0]
        assert "阳极氧化铝" in str(first["visual_prompt"])


class TestIdentityLock:
    """§29 — product identity control, per shot."""

    def test_product_dominant_shots_are_locked_by_default(self, client: TestClient) -> None:
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        produced = shots(user, workspace_id, project_id, str(storyboard["id"]))

        hero = [shot for shot in produced if shot["shot_type"] == "PRODUCT_HERO"]
        assert hero
        assert all(shot["identity_lock"] for shot in hero)

    def test_a_locked_shot_carries_the_consistency_rules(self, client: TestClient) -> None:
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        produced = shots(user, workspace_id, project_id, str(storyboard["id"]))

        locked = next(shot for shot in produced if shot["identity_lock"])
        prompt = str(locked["visual_prompt"])
        assert "CONSISTENCY RULES:" in prompt
        assert "keep the exact uploaded product identity" in prompt
        assert "do not alter visible text" in prompt

    def test_a_locked_shot_gets_identity_references_from_the_product(
        self, client: TestClient
    ) -> None:
        """A lock over nothing is not a lock. §29's references are what QC
        will compare generated frames against."""
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        produced = shots(user, workspace_id, project_id, str(storyboard["id"]))

        locked = next(shot for shot in produced if shot["identity_lock"])
        references = locked["references"]
        assert references
        assert all(item["reference_role"] == "IDENTITY" for item in references)

    def test_turning_the_lock_off_removes_the_rules(self, client: TestClient) -> None:
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        locked = next(
            shot
            for shot in shots(user, workspace_id, project_id, str(storyboard["id"]))
            if shot["identity_lock"]
        )

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/storyboards/{storyboard['id']}/shots/{locked['id']}",
            headers=user.auth,
            json={"identity_lock": False},
        )
        assert response.status_code == 200, response.text
        assert "CONSISTENCY RULES:" not in response.json()["visual_prompt"]

    def test_the_negative_prompt_forbids_a_different_product_when_locked(
        self, client: TestClient
    ) -> None:
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()
        locked = next(
            shot
            for shot in shots(user, workspace_id, project_id, str(storyboard["id"]))
            if shot["identity_lock"]
        )
        assert "different product" in str(locked["negative_prompt"])
        assert "altered logo" in str(locked["negative_prompt"])


class TestApproval:
    def test_approving_supersedes_earlier_versions(self, client: TestClient) -> None:
        """PHASE 9 asks "which storyboard?" and that needs one answer."""
        user, workspace_id, project_id = ready_project(client)
        first = generate_storyboard(user, workspace_id, project_id).json()
        second = generate_storyboard(user, workspace_id, project_id).json()

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/storyboards/{second['id']}/approve",
            headers=user.auth,
        )
        assert response.status_code == 200, response.text

        history = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/storyboards",
            headers=user.auth,
        ).json()
        statuses = {item["id"]: item["status"] for item in history}
        assert statuses[second["id"]] == "APPROVED"
        assert statuses[first["id"]] == "SUPERSEDED"

    def test_approving_rechecks_the_duration_after_edits(self, client: TestClient) -> None:
        """The last moment before PHASE 9 starts spending money.

        A storyboard edited down to a fraction of its target must not be
        approvable — the renderer would produce a video of the wrong length.
        """
        user, workspace_id, project_id = ready_project(client)
        storyboard = generate_storyboard(user, workspace_id, project_id).json()

        for shot in shots(user, workspace_id, project_id, str(storyboard["id"])):
            user.client.patch(
                f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
                f"/storyboards/{storyboard['id']}/shots/{shot['id']}",
                headers=user.auth,
                json={"duration_seconds": 2},
            )

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/storyboards/{storyboard['id']}/approve",
            headers=user.auth,
        )
        assert response.status_code == 422, response.text
        assert "target" in response.json()["error"]["message"]


class TestFailureHandling:
    @pytest.mark.parametrize("mode", ["unavailable", "rate_limited", "malformed"])
    def test_a_provider_failure_writes_no_storyboard(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, llm_mode: None, mode: str
    ) -> None:
        """§24 — a failure must not leave a half-built storyboard behind."""
        user, workspace_id, project_id = ready_project(client)

        monkeypatch.setenv("MOCK_LLM_MODE", mode)
        get_settings.cache_clear()

        response = generate_storyboard(user, workspace_id, project_id)
        assert response.status_code == 502, response.text

        history = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/storyboards",
            headers=user.auth,
        ).json()
        assert history == []


class TestAuthorisation:
    def test_a_viewer_cannot_generate_a_storyboard(self, client: TestClient) -> None:
        """§40 — GENERATION_RUN is not implied by read access."""
        owner, workspace_id, project_id = ready_project(client, prefix="owner")
        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        assert generate_storyboard(viewer, workspace_id, project_id).status_code == 403

    def test_another_workspace_cannot_read_the_shots(self, client: TestClient) -> None:
        """§60 — a cross-tenant read is a 404, not a 403."""
        owner, workspace_id, project_id = ready_project(client, prefix="owner")
        storyboard = generate_storyboard(owner, workspace_id, project_id).json()

        outsider = register_user(client, prefix="outsider")
        outsider_workspace = sole_workspace_id(outsider)

        response = outsider.client.get(
            f"/api/v1/workspaces/{outsider_workspace}/projects/{project_id}"
            f"/storyboards/{storyboard['id']}/shots",
            headers=outsider.auth,
        )
        assert response.status_code == 404, response.text
