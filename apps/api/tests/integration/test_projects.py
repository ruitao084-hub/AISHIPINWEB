"""PHASE 7 acceptance: project, creative plans and script (§84, §16, §17, §109).

§84's criteria — create a project from a product, generate three plans, choose
one, generate a script — are the first class. Everything after it tests
**P7-T09**, which is the rule the phase exists to enforce: only VERIFIED claims
may reach a script.

The strongest assertion in the file is `test_a_suggested_claim_never_reaches_a
_script`. It creates a claim that is *not* approved, generates a script, and
checks the claim's text does not appear anywhere in it — the end-to-end version
of §109, tested through the API rather than by trusting the filter's docstring.
"""

from __future__ import annotations

import uuid

import httpx2
import pytest
from conftest import ApiUser, add_member, register_user, sole_workspace_id
from fastapi.testclient import TestClient

from backend_core.config import get_settings


@pytest.fixture
def llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a test choose the mock's failure mode (§172)."""
    get_settings.cache_clear()
    yield  # type: ignore[misc]
    monkeypatch.undo()
    get_settings.cache_clear()


def create_product(user: ApiUser, workspace_id: uuid.UUID) -> str:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products",
        headers=user.auth,
        json={"name": "静音空气净化器", "category": "家用电器"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def add_verified_fact(
    user: ApiUser,
    workspace_id: uuid.UUID,
    product_id: str,
    *,
    key: str = "materials",
    value_text: str = "阳极氧化铝",
) -> str:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/facts",
        headers=user.auth,
        json={
            "fact_type": "MATERIAL",
            "key": key,
            "value_text": value_text,
            "verify": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["verification_status"] == "VERIFIED"
    return str(body["id"])


def add_claim(
    user: ApiUser,
    workspace_id: uuid.UUID,
    product_id: str,
    *,
    claim_text: str,
    source_fact_ids: list[str],
    approve: bool,
) -> str:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims",
        headers=user.auth,
        json={
            "claim_text": claim_text,
            "claim_type": "FUNCTIONAL",
            "source_fact_ids": source_fact_ids,
        },
    )
    assert response.status_code == 201, response.text
    claim_id = str(response.json()["id"])

    if approve:
        approved = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/{claim_id}/verify",
            headers=user.auth,
        )
        assert approved.status_code == 200, approved.text
    return claim_id


def create_project(
    user: ApiUser, workspace_id: uuid.UUID, product_id: str, **extra: object
) -> dict[str, object]:
    response = user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        headers=user.auth,
        json={
            "product_id": product_id,
            "name": "净化器 30 秒短片",
            "purpose": "SOCIAL_AD",
            "target_platform": "DOUYIN",
            "duration_seconds": 30,
            "style": "CLEAN_MINIMAL",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def generate_plans(user: ApiUser, workspace_id: uuid.UUID, project_id: str) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/creative-plans",
        headers=user.auth,
    )


def generate_script(user: ApiUser, workspace_id: uuid.UUID, project_id: str) -> httpx2.Response:
    return user.client.post(
        f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts", headers=user.auth
    )


def ready_project(client: TestClient, *, prefix: str = "pm") -> tuple[ApiUser, uuid.UUID, str, str]:
    """A user, workspace, product with one verified fact, and a project."""
    user = register_user(client, prefix=prefix)
    workspace_id = sole_workspace_id(user)
    product_id = create_product(user, workspace_id)
    add_verified_fact(user, workspace_id, product_id)
    project = create_project(user, workspace_id, product_id)
    return user, workspace_id, product_id, str(project["id"])


class TestAcceptance:
    """§84: product → project → 3 plans → choose → script."""

    def test_the_whole_flow(self, client: TestClient) -> None:
        user, workspace_id, _product_id, project_id = ready_project(client)

        plans_response = generate_plans(user, workspace_id, project_id)
        assert plans_response.status_code == 201, plans_response.text
        plans = plans_response.json()
        assert len(plans) == 3
        assert len({plan["title"] for plan in plans}) == 3
        assert all(not plan["selected"] for plan in plans)

        project = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}", headers=user.auth
        ).json()
        assert project["status"] == "CREATIVE_PLANNING"

        chosen = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[1]['id']}/select",
            headers=user.auth,
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["selected"] is True

        script_response = generate_script(user, workspace_id, project_id)
        assert script_response.status_code == 201, script_response.text
        script = script_response.json()

        assert script["version"] == 1
        assert script["status"] == "DRAFT"
        assert len(script["content_json"]["sections"]) == 9
        assert script["plain_text"]
        assert script["estimated_duration_seconds"] is not None

        project = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}", headers=user.auth
        ).json()
        assert project["status"] == "SCRIPTING"

    def test_a_project_starts_in_draft(self, client: TestClient) -> None:
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = create_product(user, workspace_id)
        project = create_project(user, workspace_id, product_id)
        assert project["status"] == "DRAFT"

    def test_only_one_plan_can_be_selected(self, client: TestClient) -> None:
        """§16 requires *a* choice; two simultaneous choices is not a state the
        script engine could act on."""
        user, workspace_id, _, project_id = ready_project(client)
        plans = generate_plans(user, workspace_id, project_id).json()

        for plan in plans[:2]:
            user.client.post(
                f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
                f"/creative-plans/{plan['id']}/select",
                headers=user.auth,
            )

        current = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/creative-plans",
            headers=user.auth,
        ).json()
        assert sum(1 for plan in current if plan["selected"]) == 1

    def test_regenerating_keeps_the_old_plans_and_clears_the_choice(
        self, client: TestClient
    ) -> None:
        """§103 rule 9 keeps history; a stale selection pointing at round one
        would silently drive the next script."""
        user, workspace_id, _, project_id = ready_project(client)
        first = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{first[0]['id']}/select",
            headers=user.auth,
        )

        generate_plans(user, workspace_id, project_id)

        all_plans = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/creative-plans",
            headers=user.auth,
        ).json()
        assert len(all_plans) == 6
        assert {plan["version"] for plan in all_plans} == {1, 2}
        assert not any(plan["selected"] for plan in all_plans)


class TestVerifiedClaimFilter:
    """P7-T09 and §109 — the rule this phase exists to enforce."""

    def test_a_suggested_claim_never_reaches_a_script(self, client: TestClient) -> None:
        """The end-to-end statement of §109.

        An unapproved claim exists on the product. A script is generated. The
        claim's words must not appear in it — not in the narration, not in the
        visuals, not anywhere. This is the assertion that would catch a
        regression in the filter no matter how the filter was rewritten.
        """
        user, workspace_id, product_id, project_id = ready_project(client)

        secret = "国家级实验室认证除甲醛率百分之九十九点九"
        add_claim(
            user,
            workspace_id,
            product_id,
            claim_text=secret,
            source_fact_ids=[],
            approve=False,
        )

        plans = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[0]['id']}/select",
            headers=user.auth,
        )
        script = generate_script(user, workspace_id, project_id).json()

        serialized = str(script["content_json"]) + script["plain_text"]
        assert secret not in serialized
        assert script["sourced_claim_ids"] == []

    def test_an_approved_claim_does_reach_the_script_and_is_recorded(
        self, client: TestClient
    ) -> None:
        """The other half: the filter is a filter, not a blockade."""
        user, workspace_id, product_id, project_id = ready_project(client)
        fact_id = add_verified_fact(
            user, workspace_id, product_id, key="finish", value_text="哑光表面"
        )
        claim_id = add_claim(
            user,
            workspace_id,
            product_id,
            claim_text="外观简洁，容易融入家居环境",
            source_fact_ids=[fact_id],
            approve=True,
        )

        plans = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[0]['id']}/select",
            headers=user.auth,
        )
        script = generate_script(user, workspace_id, project_id).json()

        assert script["sourced_claim_ids"] == [claim_id]

    def test_withdrawing_a_claim_leaves_the_script_traceable_to_it(
        self, client: TestClient
    ) -> None:
        """Why `sourced_claim_ids` is stored rather than recomputed.

        A claim rejected *after* a script was written is the case that matters:
        the script still says what it said, and somebody has to be able to find
        it. Recomputing from current state would lose exactly that.
        """
        user, workspace_id, product_id, project_id = ready_project(client)
        fact_id = add_verified_fact(
            user, workspace_id, product_id, key="finish", value_text="哑光表面"
        )
        claim_id = add_claim(
            user,
            workspace_id,
            product_id,
            claim_text="外观简洁",
            source_fact_ids=[fact_id],
            approve=True,
        )

        plans = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[0]['id']}/select",
            headers=user.auth,
        )
        script = generate_script(user, workspace_id, project_id).json()
        assert claim_id in script["sourced_claim_ids"]

        rejected = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/products/{product_id}/claims/{claim_id}/reject",
            headers=user.auth,
        )
        assert rejected.status_code == 200, rejected.text

        scripts = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts",
            headers=user.auth,
        ).json()
        assert claim_id in scripts[0]["sourced_claim_ids"]

    def test_a_product_with_nothing_verified_is_refused(self, client: TestClient) -> None:
        """§13 — a video built from nothing confirmed would be a video built
        from the model's imagination."""
        user = register_user(client, prefix="pm")
        workspace_id = sole_workspace_id(user)
        product_id = create_product(user, workspace_id)
        project = create_project(user, workspace_id, product_id)

        response = generate_plans(user, workspace_id, str(project["id"]))
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CLAIM_NOT_VERIFIED"


class TestScriptVersioning:
    """§17, P7-T10 — history survives an edit."""

    def _scripted(self, client: TestClient) -> tuple[ApiUser, uuid.UUID, str, dict[str, object]]:
        user, workspace_id, _, project_id = ready_project(client)
        plans = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[0]['id']}/select",
            headers=user.auth,
        )
        script = generate_script(user, workspace_id, project_id).json()
        return user, workspace_id, project_id, script

    def test_regenerating_produces_a_new_version_not_an_overwrite(self, client: TestClient) -> None:
        user, workspace_id, project_id, first = self._scripted(client)
        second = generate_script(user, workspace_id, project_id).json()

        assert second["version"] == 2
        history = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts",
            headers=user.auth,
        ).json()
        assert [item["version"] for item in history] == [2, 1]
        assert first["id"] in {item["id"] for item in history}

    def test_a_human_edit_is_saved_as_a_new_version(self, client: TestClient) -> None:
        """§17: a user who edits and regrets it must see what they had."""
        user, workspace_id, project_id, first = self._scripted(client)

        document = dict(first["content_json"])  # type: ignore[arg-type]
        sections = [dict(section) for section in document["sections"]]
        sections[0]["narration"] = "手写的开场白。"
        document["sections"] = sections

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts/revise",
            headers=user.auth,
            json={"document": document},
        )
        assert response.status_code == 201, response.text
        revised = response.json()

        assert revised["version"] == 2
        assert "手写的开场白。" in revised["plain_text"]

        original = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts",
            headers=user.auth,
        ).json()[-1]
        assert original["version"] == 1
        assert "手写的开场白。" not in original["plain_text"]

    def test_a_hand_written_script_must_still_satisfy_the_schema(self, client: TestClient) -> None:
        """An eight-section script would break PHASE 8 exactly as a generated
        one would, so the human path is validated identically."""
        user, workspace_id, project_id, first = self._scripted(client)

        document = dict(first["content_json"])  # type: ignore[arg-type]
        document["sections"] = list(document["sections"])[:-1]

        response = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts/revise",
            headers=user.auth,
            json={"document": document},
        )
        assert response.status_code == 422, response.text

    def test_approving_supersedes_every_other_version(self, client: TestClient) -> None:
        """PHASE 8 asks "which script?" and that must have one answer."""
        user, workspace_id, project_id, first = self._scripted(client)
        second = generate_script(user, workspace_id, project_id).json()

        approved = user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/scripts/{second['id']}/approve",
            headers=user.auth,
        )
        assert approved.status_code == 200, approved.text

        history = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/scripts",
            headers=user.auth,
        ).json()
        statuses = {item["id"]: item["status"] for item in history}
        assert statuses[second["id"]] == "APPROVED"
        assert statuses[first["id"]] == "SUPERSEDED"

    def test_approving_moves_the_project_to_storyboarding(self, client: TestClient) -> None:
        user, workspace_id, project_id, script = self._scripted(client)
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/scripts/{script['id']}/approve",
            headers=user.auth,
        )
        project = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}", headers=user.auth
        ).json()
        assert project["status"] == "STORYBOARDING"


class TestStateMachine:
    def test_a_script_cannot_be_generated_before_a_plan_is_chosen(self, client: TestClient) -> None:
        """§16 makes the choice mandatory, not advisory."""
        user, workspace_id, _, project_id = ready_project(client)
        generate_plans(user, workspace_id, project_id)

        response = generate_script(user, workspace_id, project_id)
        assert response.status_code == 422, response.text

    def test_the_brief_cannot_be_edited_once_generation_has_started(
        self, client: TestClient
    ) -> None:
        """Changing the duration after shots exist would orphan paid work."""
        user, workspace_id, _, project_id = ready_project(client)
        plans = generate_plans(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/creative-plans/{plans[0]['id']}/select",
            headers=user.auth,
        )
        script = generate_script(user, workspace_id, project_id).json()
        user.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}"
            f"/scripts/{script['id']}/approve",
            headers=user.auth,
        )

        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}",
            headers=user.auth,
            json={"duration_seconds": 60},
        )
        assert response.status_code == 409, response.text

    def test_the_brief_can_be_edited_while_still_drafting(self, client: TestClient) -> None:
        user, workspace_id, _, project_id = ready_project(client)
        response = user.client.patch(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}",
            headers=user.auth,
            json={"duration_seconds": 45, "name": "改过的名字"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["duration_seconds"] == 45


class TestFailureHandling:
    @pytest.mark.parametrize("mode", ["unavailable", "rate_limited", "rejected", "malformed"])
    def test_a_provider_failure_leaves_the_project_where_it_was(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, llm_mode: None, mode: str
    ) -> None:
        """§24 — a failure must not strand a project mid-state."""
        user, workspace_id, _, project_id = ready_project(client)

        monkeypatch.setenv("MOCK_LLM_MODE", mode)
        get_settings.cache_clear()

        response = generate_plans(user, workspace_id, project_id)
        assert response.status_code == 502, response.text

        project = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}", headers=user.auth
        ).json()
        assert project["status"] == "DRAFT"

        plans = user.client.get(
            f"/api/v1/workspaces/{workspace_id}/projects/{project_id}/creative-plans",
            headers=user.auth,
        ).json()
        assert plans == []


class TestAuthorisation:
    def test_a_viewer_cannot_spend_the_workspaces_money(self, client: TestClient) -> None:
        """§40 — GENERATION_RUN is not implied by read access."""
        owner, workspace_id, _, project_id = ready_project(client, prefix="owner")
        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        assert generate_plans(viewer, workspace_id, project_id).status_code == 403

    def test_a_viewer_cannot_create_a_project(self, client: TestClient) -> None:
        owner, workspace_id, product_id, _ = ready_project(client, prefix="owner")
        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        response = viewer.client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            headers=viewer.auth,
            json={"product_id": product_id, "name": "x", "duration_seconds": 30},
        )
        assert response.status_code == 403, response.text

    def test_another_workspace_cannot_read_this_project(self, client: TestClient) -> None:
        """§60 — a cross-tenant read is a 404, not a 403."""
        _owner, _workspace_id, _product_id, project_id = ready_project(client, prefix="owner")
        outsider = register_user(client, prefix="outsider")
        outsider_workspace = sole_workspace_id(outsider)

        response = outsider.client.get(
            f"/api/v1/workspaces/{outsider_workspace}/projects/{project_id}",
            headers=outsider.auth,
        )
        assert response.status_code == 404, response.text
