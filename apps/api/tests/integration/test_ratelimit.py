"""Login and registration rate limiting still bites (taskbook §39, §123).

The suite clears these counters between tests so the limits do not fail
unrelated cases. That makes it essential to assert here that the limits are
real — otherwise "cleared for convenience" quietly becomes "not enforced".
"""

from __future__ import annotations

from conftest import DEFAULT_PASSWORD, register_user, unique_email
from fastapi.testclient import TestClient

from backend_core.security import LOGIN_ACCOUNT_LIMIT, REGISTER_IP_LIMIT


class TestLoginRateLimit:
    def test_repeated_failures_are_eventually_blocked(self, client: TestClient) -> None:
        """Brute force must stop being cheap after a handful of guesses."""
        user = register_user(client)

        statuses = [
            client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": f"wrong-guess-{attempt}"},
            ).status_code
            for attempt in range(LOGIN_ACCOUNT_LIMIT.max_attempts + 2)
        ]

        assert 401 in statuses, "expected some attempts to be rejected as wrong"
        assert 429 in statuses, "expected the limiter to engage"
        # Once blocked, it stays blocked within the window.
        assert statuses[-1] == 429

    def test_the_limit_applies_to_unknown_accounts_too(self, client: TestClient) -> None:
        """Otherwise an attacker probes freely by varying the address."""
        email = unique_email("ghost")
        statuses = [
            client.post(
                "/api/v1/auth/login", json={"email": email, "password": "guess"}
            ).status_code
            for _ in range(LOGIN_ACCOUNT_LIMIT.max_attempts + 2)
        ]
        assert 429 in statuses

    def test_a_successful_login_clears_the_counter(self, client: TestClient) -> None:
        """A user who mistypes twice must not lock themselves out."""
        user = register_user(client)

        for _ in range(LOGIN_ACCOUNT_LIMIT.max_attempts - 1):
            client.post(
                "/api/v1/auth/login",
                json={"email": user.email, "password": "wrong-password"},
            )

        good = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": user.password},
        )
        assert good.status_code == 200

        # The counter was reset, so a fresh mistake is merely a 401.
        again = client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "wrong-password"},
        )
        assert again.status_code == 401


class TestRegisterRateLimit:
    def test_bulk_registration_is_throttled(self, client: TestClient) -> None:
        statuses = [
            client.post(
                "/api/v1/auth/register",
                json={
                    "email": unique_email(f"bulk{index}"),
                    "password": DEFAULT_PASSWORD,
                    "display_name": "Bulk",
                },
            ).status_code
            for index in range(REGISTER_IP_LIMIT.max_attempts + 2)
        ]
        assert 201 in statuses
        assert 429 in statuses
