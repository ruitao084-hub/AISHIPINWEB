"""Request correlation (§63) and the versioned API surface (§41)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aipvs_api.app import create_app
from aipvs_api.middleware import REQUEST_ID_HEADER


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestRequestId:
    def test_a_request_id_is_always_returned(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers[REQUEST_ID_HEADER]

    def test_ids_differ_between_requests(self, client: TestClient) -> None:
        first = client.get("/health").headers[REQUEST_ID_HEADER]
        second = client.get("/health").headers[REQUEST_ID_HEADER]
        assert first != second

    def test_a_caller_supplied_id_is_honoured(self, client: TestClient) -> None:
        """Lets one trace span the web app and the API."""
        response = client.get("/health", headers={REQUEST_ID_HEADER: "web-abc-123"})
        assert response.headers[REQUEST_ID_HEADER] == "web-abc-123"

    @pytest.mark.parametrize(
        "hostile",
        [
            "abc\ndef",  # forge extra JSON log lines
            "abc\r\nSet-Cookie: x=1",  # header injection
            "id with spaces",
            "a" * 200,  # bloat every log record
            "../../etc/passwd",
            "<script>alert(1)</script>",
        ],
    )
    def test_malformed_ids_are_replaced_not_echoed(self, client: TestClient, hostile: str) -> None:
        """The id lands in every log line, so it is untrusted input."""
        response = client.get("/health", headers={REQUEST_ID_HEADER: hostile})
        returned = response.headers[REQUEST_ID_HEADER]
        assert returned != hostile
        assert "\n" not in returned and "\r" not in returned
        assert len(returned) <= 128


class TestApiV1:
    def test_v1_root_reports_version(self, client: TestClient) -> None:
        response = client.get("/api/v1/")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "v1"
        assert body["service"] == "aipvs-api"

    def test_feature_flags_are_advertised(self, client: TestClient) -> None:
        """§122 — clients adapt rather than probing disabled endpoints."""
        features = client.get("/api/v1/").json()["features"]
        assert isinstance(features, list)
        # Mocks are the default so a developer needs no keys (§170).
        assert "mock_providers" in features

    def test_business_routes_are_versioned(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        business = [path for path in schema["paths"] if path not in {"/health", "/ready"}]
        assert business, "expected at least one business route"
        assert all(path.startswith("/api/v1") for path in business), business

    def test_health_stays_outside_the_versioned_surface(self, client: TestClient) -> None:
        """An orchestrator probe should not follow API versioning (§70)."""
        schema = client.get("/openapi.json").json()
        assert "/health" in schema["paths"]
        assert "/ready" in schema["paths"]


class TestOpenApiContract:
    def test_error_envelope_is_documented(self, client: TestClient) -> None:
        """The generated TS client must know failures have one shape (§5.2)."""
        schema = client.get("/openapi.json").json()
        assert "ErrorResponse" in schema["components"]["schemas"]

        responses = schema["paths"]["/api/v1/"]["get"]["responses"]
        for status_code in ("401", "403", "404", "422", "500"):
            ref = responses[status_code]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse")
