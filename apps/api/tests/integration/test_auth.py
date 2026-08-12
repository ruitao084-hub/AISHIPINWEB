"""PHASE 3 acceptance: register, login, refresh (taskbook §80)."""

from __future__ import annotations

from conftest import (
    DEFAULT_PASSWORD,
    register_user,
    sole_workspace_id,
    unique_email,
)
from fastapi.testclient import TestClient

from aipvs_api.v1.auth import REFRESH_COOKIE_NAME


class TestRegister:
    def test_registration_returns_tokens_and_user(self, client: TestClient) -> None:
        user = register_user(client, prefix="alice")
        assert user.access_token
        assert user.email.startswith("alice-")

    def test_registration_creates_a_personal_workspace(self, client: TestClient) -> None:
        """A user with no workspace cannot use the product at all."""
        user = register_user(client)
        workspace_id = sole_workspace_id(user)

        response = client.get(f"/api/v1/workspaces/{workspace_id}", headers=user.auth)
        assert response.status_code == 200
        assert response.json()["role"] == "OWNER"

    def test_duplicate_email_is_rejected(self, client: TestClient) -> None:
        email = unique_email()
        payload = {"email": email, "password": DEFAULT_PASSWORD, "display_name": "A"}
        assert client.post("/api/v1/auth/register", json=payload).status_code == 201

        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 409
        assert response.json()["error"]["code"]

    def test_email_is_case_insensitive(self, client: TestClient) -> None:
        """Otherwise one mailbox yields two accounts — a reset-flow takeover."""
        email = unique_email()
        payload = {"email": email, "password": DEFAULT_PASSWORD, "display_name": "A"}
        assert client.post("/api/v1/auth/register", json=payload).status_code == 201

        upper = {**payload, "email": email.upper()}
        assert client.post("/api/v1/auth/register", json=upper).status_code == 409

    def test_short_password_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": unique_email(), "password": "short", "display_name": "A"},
        )
        assert response.status_code == 422

    def test_password_is_never_echoed(self, client: TestClient) -> None:
        secret = "a-very-distinctive-password-123"
        response = client.post(
            "/api/v1/auth/register",
            json={"email": unique_email(), "password": secret, "display_name": "A"},
        )
        assert secret not in response.text

    def test_refresh_token_is_httponly_and_not_in_the_body(self, client: TestClient) -> None:
        """§39 — an XSS bug must not be able to read a month-long credential."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": unique_email(),
                "password": DEFAULT_PASSWORD,
                "display_name": "A",
            },
        )
        assert "refresh_token" not in response.json()

        set_cookie = response.headers.get("set-cookie", "")
        assert REFRESH_COOKIE_NAME in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie.replace("samesite", "SameSite")


class TestLogin:
    def test_login_succeeds_with_correct_credentials(self, client: TestClient) -> None:
        user = register_user(client)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": user.password},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_login_is_case_insensitive_on_email(self, client: TestClient) -> None:
        user = register_user(client)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": user.email.upper(), "password": user.password},
        )
        assert response.status_code == 200

    def test_wrong_password_is_rejected(self, client: TestClient) -> None:
        user = register_user(client)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "wrong-password-entirely"},
        )
        assert response.status_code == 401

    def test_unknown_and_wrong_password_are_indistinguishable(self, client: TestClient) -> None:
        """Any difference turns login into an account-enumeration oracle."""
        user = register_user(client)

        wrong = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "wrong-password-entirely"},
        )
        missing = client.post(
            "/api/v1/auth/login",
            json={"email": unique_email("nobody"), "password": "wrong-password-entirely"},
        )

        assert wrong.status_code == missing.status_code == 401
        assert wrong.json()["error"]["code"] == missing.json()["error"]["code"]
        assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]


class TestRefresh:
    def test_refresh_issues_a_new_access_token(self, client: TestClient) -> None:
        register_user(client)  # sets the refresh cookie on the client
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_refresh_without_a_cookie_is_unauthorized(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 401

    def test_a_rotated_refresh_token_cannot_be_reused(self, client: TestClient) -> None:
        """Rotation makes a stolen refresh token usable at most once."""
        register_user(client)
        stolen = client.cookies.get(REFRESH_COOKIE_NAME)
        assert stolen

        assert client.post("/api/v1/auth/refresh").status_code == 200

        # Replay the original token after it has been rotated away.
        client.cookies.set(REFRESH_COOKIE_NAME, stolen)
        assert client.post("/api/v1/auth/refresh").status_code == 401

    def test_an_access_token_is_not_accepted_as_a_refresh_token(self, client: TestClient) -> None:
        """Otherwise a 15-minute credential buys a 30-day session."""
        user = register_user(client)
        client.cookies.set(REFRESH_COOKIE_NAME, user.access_token)
        assert client.post("/api/v1/auth/refresh").status_code == 401


class TestLogoutAndMe:
    def test_me_returns_the_signed_in_user(self, client: TestClient) -> None:
        user = register_user(client)
        response = client.get("/api/v1/auth/me", headers=user.auth)
        assert response.status_code == 200
        assert response.json()["email"] == user.email

    def test_me_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_me_rejects_a_garbage_token(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    def test_logout_revokes_the_refresh_token(self, client: TestClient) -> None:
        register_user(client)
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.post("/api/v1/auth/refresh").status_code == 401

    def test_logout_succeeds_without_a_session(self, client: TestClient) -> None:
        """A logout that errors leaves users believing they signed out."""
        assert client.post("/api/v1/auth/logout").status_code == 204


class TestPasswordStorage:
    async def test_password_is_never_stored_in_plaintext(self, client: TestClient) -> None:
        from sqlalchemy import select

        from backend_core.db import get_async_session
        from backend_core.domain.models import User

        user = register_user(client)

        async with get_async_session() as session:
            result = await session.execute(select(User).where(User.id == user.id))
            stored = result.scalar_one()

        assert stored.password_hash != user.password
        assert user.password not in stored.password_hash
        # Argon2id, per §39 and ADR-0006.
        assert stored.password_hash.startswith("$argon2id$")
