"""Tests for the liveness and readiness probes (taskbook §70)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aipvs_api.app import create_app


def test_health_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "aipvs-api"


def test_ready_reports_ready_when_no_dependencies_are_registered() -> None:
    """With no backing services wired yet, readiness is vacuously true."""
    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["dependencies"] == []


def test_openapi_document_is_served() -> None:
    """§5.2 makes this document the contract source for the generated TS client."""
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "AI Product Video Studio API"
    assert "/health" in schema["paths"]
    assert "/ready" in schema["paths"]
