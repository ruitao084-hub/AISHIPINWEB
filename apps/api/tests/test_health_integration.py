"""PHASE 1 acceptance: the API reports healthy against real services (§78)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aipvs_api.app import create_app
from backend_core.storage import get_storage

pytestmark = pytest.mark.integration


def test_ready_reports_every_dependency_up() -> None:
    """The taskbook's PHASE 1 acceptance, end to end through HTTP."""
    get_storage().ensure_bucket()

    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True

    by_name = {d["name"]: d for d in body["dependencies"]}
    assert set(by_name) == {"database", "redis", "storage"}
    for name, status in by_name.items():
        assert status["ready"] is True, f"{name} not ready: {status}"
