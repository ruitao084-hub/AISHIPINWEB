"""Every failure path returns the §41 envelope, and leaks nothing (§63)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from aipvs_api.app import create_app
from aipvs_api.middleware import REQUEST_ID_HEADER
from backend_core.errors import ErrorCode, NotFoundError, ProviderRateLimitedError


class Body(BaseModel):
    name: str
    count: int


@pytest.fixture
def app() -> FastAPI:
    """An app with routes that fail in each of the ways a client can meet."""
    application = create_app()

    @application.get("/_test/app-error")
    async def _app_error() -> None:
        raise NotFoundError("Project not found.")

    @application.get("/_test/app-error-details")
    async def _app_error_details() -> None:
        raise ProviderRateLimitedError(details={"retry_after_seconds": 30})

    @application.get("/_test/boom")
    async def _boom() -> None:
        raise RuntimeError("connect failed: postgresql://svc:hunter2@db:5432/aipvs")

    @application.post("/_test/validated")
    async def _validated(body: Body) -> dict[str, str]:
        return {"name": body.name}

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False so the 500 handler runs, as in production,
    # instead of the exception surfacing into the test.
    return TestClient(app, raise_server_exceptions=False)


def assert_envelope(payload: dict[str, object]) -> dict[str, object]:
    """Every error response has exactly this shape (§41)."""
    assert set(payload) == {"error"}
    error = payload["error"]
    assert isinstance(error, dict)
    assert isinstance(error["code"], str)
    assert isinstance(error["message"], str)
    assert error["code"] in {c.value for c in ErrorCode}
    return error


class TestApplicationErrors:
    def test_app_error_renders_its_code_and_status(self, client: TestClient) -> None:
        response = client.get("/_test/app-error")
        assert response.status_code == 404
        error = assert_envelope(response.json())
        assert error["code"] == "PRODUCT_NOT_FOUND"
        assert error["message"] == "Project not found."

    def test_details_are_passed_through(self, client: TestClient) -> None:
        response = client.get("/_test/app-error-details")
        assert response.status_code == 429
        error = assert_envelope(response.json())
        assert error["details"] == {"retry_after_seconds": 30}

    def test_request_id_is_included_and_matches_the_header(self, client: TestClient) -> None:
        """The id a user quotes must find the request in the logs."""
        response = client.get("/_test/app-error")
        error = assert_envelope(response.json())
        assert error["request_id"] == response.headers[REQUEST_ID_HEADER]


class TestUnhandledErrors:
    def test_unexpected_exception_becomes_a_clean_500(self, client: TestClient) -> None:
        response = client.get("/_test/boom")
        assert response.status_code == 500
        error = assert_envelope(response.json())
        assert error["code"] == "INTERNAL_ERROR"

    def test_internals_never_reach_the_client(self, client: TestClient) -> None:
        """A traceback carries table names, paths and connection passwords."""
        response = client.get("/_test/boom")
        body = response.text
        assert "hunter2" not in body
        assert "postgresql://" not in body
        assert "RuntimeError" not in body
        assert "Traceback" not in body

    def test_a_500_still_carries_a_request_id(self, client: TestClient) -> None:
        error = assert_envelope(client.get("/_test/boom").json())
        assert error["request_id"]


class TestFrameworkErrors:
    """Paths a developer never writes, which must still match the envelope."""

    def test_404_from_the_router(self, client: TestClient) -> None:
        response = client.get("/_test/does-not-exist")
        assert response.status_code == 404
        assert_envelope(response.json())

    def test_405_method_not_allowed(self, client: TestClient) -> None:
        response = client.delete("/_test/validated")
        assert response.status_code == 405
        assert_envelope(response.json())

    def test_validation_error_uses_the_envelope(self, client: TestClient) -> None:
        response = client.post("/_test/validated", json={"name": "x"})
        assert response.status_code == 422
        error = assert_envelope(response.json())
        assert error["code"] == "VALIDATION_ERROR"

    def test_validation_details_name_the_field_without_echoing_the_value(
        self, client: TestClient
    ) -> None:
        """Echoing input back leaks a submitted password into logs and screens."""
        response = client.post("/_test/validated", json={"name": "x", "count": "hunter2"})
        assert response.status_code == 422
        error = assert_envelope(response.json())
        details = error["details"]
        assert isinstance(details, dict)
        fields = details["fields"]
        assert isinstance(fields, list)
        assert any("count" in field["field"] for field in fields)
        assert "hunter2" not in response.text


class TestSuccessPath:
    def test_successful_responses_are_untouched(self, client: TestClient) -> None:
        response = client.post("/_test/validated", json={"name": "x", "count": 1})
        assert response.status_code == 200
        assert response.json() == {"name": "x"}
