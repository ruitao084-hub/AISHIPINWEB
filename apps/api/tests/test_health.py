"""Liveness and readiness endpoint behaviour (taskbook §70).

These are unit tests: the dependency probes are substituted so the endpoint's
own logic is verified without any service running. Real connectivity is proven
by the integration suite.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from aipvs_api import health
from aipvs_api.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _all_probes(monkeypatch: pytest.MonkeyPatch, *, healthy: bool) -> None:
    async def fake_async() -> bool:
        return healthy

    def fake_sync() -> bool:
        return healthy

    monkeypatch.setattr(health, "ping_database", fake_async)
    monkeypatch.setattr(health, "ping_redis", fake_async)
    monkeypatch.setattr(health, "ping_storage", fake_sync)


class TestLiveness:
    def test_health_reports_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "aipvs-api"

    def test_health_ignores_broken_dependencies(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Liveness must not depend on backing services.

        If it did, a transient Postgres blip would get the container killed and
        restarted rather than merely drained from rotation.
        """
        _all_probes(monkeypatch, healthy=False)
        assert client.get("/health").status_code == 200


class TestReadiness:
    def test_ready_returns_200_when_all_dependencies_are_up(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _all_probes(monkeypatch, healthy=True)

        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert {d["name"] for d in body["dependencies"]} == {
            "database",
            "redis",
            "storage",
        }
        assert all(d["ready"] for d in body["dependencies"])

    def test_ready_returns_503_when_a_dependency_is_down(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _all_probes(monkeypatch, healthy=True)

        async def failing() -> bool:
            return False

        monkeypatch.setattr(health, "ping_redis", failing)

        response = client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False
        failed = [d for d in body["dependencies"] if not d["ready"]]
        assert [d["name"] for d in failed] == ["redis"]

    def test_a_raising_probe_is_reported_not_propagated(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A probe that throws must yield 503, never a 500 from the endpoint."""
        _all_probes(monkeypatch, healthy=True)

        async def exploding() -> bool:
            raise ConnectionRefusedError("no route to host")

        monkeypatch.setattr(health, "ping_database", exploding)

        response = client.get("/ready")
        assert response.status_code == 503
        database = next(d for d in response.json()["dependencies"] if d["name"] == "database")
        assert database["ready"] is False
        assert database["detail"] == "ConnectionRefusedError"

    def test_probe_detail_never_leaks_the_exception_message(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§63 — connection strings and credentials must not reach a response."""
        _all_probes(monkeypatch, healthy=True)

        async def leaky() -> bool:
            raise ConnectionError("could not connect to postgresql://user:hunter2@db:5432/aipvs")

        monkeypatch.setattr(health, "ping_database", leaky)

        response = client.get("/ready")
        assert "hunter2" not in response.text
        assert "postgresql://" not in response.text

    def test_a_hanging_probe_times_out(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stuck dependency must not hang the probe past its budget."""
        _all_probes(monkeypatch, healthy=True)
        monkeypatch.setattr(health, "_PROBE_TIMEOUT_SECONDS", 0.05)

        async def hanging() -> bool:
            # Awaited directly by _probe, so wait_for genuinely cancels it.
            # A sync probe would need a real thread, which cannot be cancelled
            # and would stall interpreter shutdown.
            await asyncio.sleep(5)
            return True

        monkeypatch.setattr(health, "ping_redis", hanging)

        response = client.get("/ready")
        assert response.status_code == 503
        redis_status = next(d for d in response.json()["dependencies"] if d["name"] == "redis")
        assert redis_status["detail"] == "probe timed out"


class TestContract:
    def test_openapi_document_is_served(self, client: TestClient) -> None:
        """§5.2 makes this document the contract source for the TS client."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "AI Product Video Studio API"
        assert "/health" in schema["paths"]
        assert "/ready" in schema["paths"]
