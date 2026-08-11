"""PHASE 3 acceptance: the role matrix, and that unauthorised access fails (§80, §40)."""

from __future__ import annotations

import uuid

import pytest
from conftest import add_member, register_user, sole_workspace_id
from fastapi.testclient import TestClient


class TestTenantIsolation:
    """§80's headline requirement, and the one that matters most."""

    def test_a_stranger_cannot_read_another_workspace(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        stranger = register_user(client, prefix="stranger")

        response = client.get(f"/api/v1/workspaces/{workspace_id}", headers=stranger.auth)
        assert response.status_code == 404

    def test_a_stranger_gets_404_not_403(self, client: TestClient) -> None:
        """403 would confirm the id exists — an existence oracle across tenants."""
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        stranger = register_user(client, prefix="stranger")

        real = client.get(f"/api/v1/workspaces/{workspace_id}", headers=stranger.auth)
        fake = client.get(f"/api/v1/workspaces/{uuid.uuid4()}", headers=stranger.auth)

        # A real-but-forbidden id must be indistinguishable from a random one.
        assert real.status_code == fake.status_code == 404
        assert real.json()["error"]["code"] == fake.json()["error"]["code"]

    def test_listing_never_includes_other_tenants_workspaces(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        owner_workspace = sole_workspace_id(owner)
        stranger = register_user(client, prefix="stranger")

        listed = client.get("/api/v1/workspaces", headers=stranger.auth).json()
        assert str(owner_workspace) not in {entry["id"] for entry in listed}

    def test_a_stranger_cannot_list_members(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        stranger = register_user(client, prefix="stranger")

        response = client.get(f"/api/v1/workspaces/{workspace_id}/members", headers=stranger.auth)
        assert response.status_code == 404

    def test_a_stranger_cannot_rename_a_workspace(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        stranger = register_user(client, prefix="stranger")

        response = client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            headers=stranger.auth,
            json={"name": "Hijacked"},
        )
        assert response.status_code == 404

    def test_a_member_id_from_another_workspace_does_not_resolve(self, client: TestClient) -> None:
        """Scoping member lookups by workspace is not redundant."""
        owner_a = register_user(client, prefix="ownera")
        workspace_a = sole_workspace_id(owner_a)
        victim = register_user(client, prefix="victim")
        member_in_a = add_member(owner_a, workspace_a, victim, "EDITOR")

        owner_b = register_user(client, prefix="ownerb")
        workspace_b = sole_workspace_id(owner_b)

        # Owner B, using a member id belonging to workspace A.
        response = client.delete(
            f"/api/v1/workspaces/{workspace_b}/members/{member_in_a}",
            headers=owner_b.auth,
        )
        assert response.status_code == 404


class TestRoleMatrix:
    """OWNER / ADMIN / EDITOR / VIEWER, enforced server-side (§40)."""

    @pytest.mark.parametrize(
        ("role", "expected"),
        [("ADMIN", 200), ("EDITOR", 403), ("VIEWER", 403)],
    )
    def test_only_admin_and_above_may_rename(
        self, client: TestClient, role: str, expected: int
    ) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        member = register_user(client, prefix=role.lower())
        add_member(owner, workspace_id, member, role)

        response = client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            headers=member.auth,
            json={"name": "Renamed"},
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        ("role", "expected"),
        [("ADMIN", 201), ("EDITOR", 403), ("VIEWER", 403)],
    )
    def test_only_admin_and_above_may_invite(
        self, client: TestClient, role: str, expected: int
    ) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        member = register_user(client, prefix=role.lower())
        add_member(owner, workspace_id, member, role)
        invitee = register_user(client, prefix="invitee")

        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=member.auth,
            json={"email": invitee.email, "role": "VIEWER"},
        )
        assert response.status_code == expected

    @pytest.mark.parametrize("role", ["ADMIN", "EDITOR", "VIEWER"])
    def test_only_owner_may_change_roles(self, client: TestClient, role: str) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        actor = register_user(client, prefix=role.lower())
        add_member(owner, workspace_id, actor, role)
        target = register_user(client, prefix="target")
        target_member_id = add_member(owner, workspace_id, target, "VIEWER")

        response = client.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{target_member_id}",
            headers=actor.auth,
            json={"role": "EDITOR"},
        )
        assert response.status_code == 403

    def test_every_role_can_read(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)

        for role in ("ADMIN", "EDITOR", "VIEWER"):
            member = register_user(client, prefix=f"read{role.lower()}")
            add_member(owner, workspace_id, member, role)
            response = client.get(f"/api/v1/workspaces/{workspace_id}", headers=member.auth)
            assert response.status_code == 200, f"{role} could not read"
            assert response.json()["role"] == role


class TestPrivilegeEscalation:
    def test_an_admin_cannot_invite_an_owner(self, client: TestClient) -> None:
        """Otherwise an admin invites an account they control and escalates."""
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        admin = register_user(client, prefix="admin")
        add_member(owner, workspace_id, admin, "ADMIN")
        accomplice = register_user(client, prefix="accomplice")

        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=admin.auth,
            json={"email": accomplice.email, "role": "OWNER"},
        )
        assert response.status_code == 403

    def test_a_member_cannot_promote_themselves(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        editor = register_user(client, prefix="editor")
        member_id = add_member(owner, workspace_id, editor, "EDITOR")

        response = client.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{member_id}",
            headers=editor.auth,
            json={"role": "OWNER"},
        )
        assert response.status_code == 403


class TestLastOwnerProtection:
    def test_the_last_owner_cannot_be_demoted(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)

        members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members", headers=owner.auth
        ).json()
        owner_member_id = next(m["id"] for m in members if m["role"] == "OWNER")

        response = client.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{owner_member_id}",
            headers=owner.auth,
            json={"role": "VIEWER"},
        )
        assert response.status_code == 409

    def test_the_last_owner_cannot_leave(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members", headers=owner.auth
        ).json()
        owner_member_id = next(m["id"] for m in members if m["role"] == "OWNER")

        response = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{owner_member_id}",
            headers=owner.auth,
        )
        assert response.status_code == 409

    def test_an_owner_may_leave_once_another_owner_exists(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        second = register_user(client, prefix="second")
        add_member(owner, workspace_id, second, "OWNER")

        members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members", headers=owner.auth
        ).json()
        first_member_id = next(m["id"] for m in members if m["user_id"] == str(owner.id))

        response = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{first_member_id}",
            headers=owner.auth,
        )
        assert response.status_code == 204


class TestMembership:
    def test_a_member_may_leave_voluntarily(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        viewer = register_user(client, prefix="viewer")
        member_id = add_member(owner, workspace_id, viewer, "VIEWER")

        assert (
            client.delete(
                f"/api/v1/workspaces/{workspace_id}/members/{member_id}",
                headers=viewer.auth,
            ).status_code
            == 204
        )
        # Access is gone immediately afterwards.
        assert (
            client.get(f"/api/v1/workspaces/{workspace_id}", headers=viewer.auth).status_code == 404
        )

    def test_adding_the_same_member_twice_is_rejected(self, client: TestClient) -> None:
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        invitee = register_user(client, prefix="invitee")
        add_member(owner, workspace_id, invitee, "VIEWER")

        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=owner.auth,
            json={"email": invitee.email, "role": "EDITOR"},
        )
        assert response.status_code == 409

    def test_permissions_are_advertised_for_the_caller(self, client: TestClient) -> None:
        """The UI hides actions with this; the server still enforces them."""
        owner = register_user(client, prefix="owner")
        workspace_id = sole_workspace_id(owner)
        viewer = register_user(client, prefix="viewer")
        add_member(owner, workspace_id, viewer, "VIEWER")

        as_owner = client.get(f"/api/v1/workspaces/{workspace_id}", headers=owner.auth).json()
        as_viewer = client.get(f"/api/v1/workspaces/{workspace_id}", headers=viewer.auth).json()

        assert "billing:manage" in as_owner["permissions"]
        assert "billing:manage" not in as_viewer["permissions"]
        assert "workspace:read" in as_viewer["permissions"]
