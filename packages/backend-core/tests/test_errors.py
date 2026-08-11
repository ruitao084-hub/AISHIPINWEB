"""Error taxonomy and retry classification (taskbook §65, §24)."""

from __future__ import annotations

import pytest

from backend_core.errors import (
    AppError,
    ClaimNotVerifiedError,
    ErrorCode,
    InsufficientCreditsError,
    InvalidCredentialsError,
    JobTimeoutError,
    NotFoundError,
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
    StorageOperationError,
    UnauthorizedError,
    WorkspaceForbiddenError,
)

# Every code §65 enumerates must exist. This test is the reason a typo in a
# code string cannot reach a client.
TASKBOOK_CODES = {
    "AUTH_INVALID_CREDENTIALS",
    "AUTH_UNAUTHORIZED",
    "WORKSPACE_FORBIDDEN",
    "PRODUCT_NOT_FOUND",
    "ASSET_INVALID",
    "UPLOAD_TOO_LARGE",
    "CLAIM_NOT_VERIFIED",
    "PROJECT_INVALID_STATE",
    "INSUFFICIENT_CREDITS",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_REJECTED",
    "JOB_TIMEOUT",
    "JOB_CANCELED",
    "RENDER_FAILED",
    "QC_FAILED",
    "STORAGE_ERROR",
    "INTERNAL_ERROR",
}


class TestTaxonomy:
    def test_every_taskbook_code_exists(self) -> None:
        assert {code.value for code in ErrorCode} >= TASKBOOK_CODES

    def test_codes_are_strings_on_the_wire(self) -> None:
        assert ErrorCode.JOB_TIMEOUT == "JOB_TIMEOUT"

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (InvalidCredentialsError(), 401),
            (UnauthorizedError(), 401),
            (WorkspaceForbiddenError(), 403),
            (NotFoundError(), 404),
            (InsufficientCreditsError(), 402),
            (ProviderRateLimitedError(), 429),
            (ProviderUnavailableError(), 503),
            (JobTimeoutError(), 504),
            (StorageOperationError(), 500),
        ],
    )
    def test_http_status_mapping(self, error: AppError, status: int) -> None:
        assert error.http_status == status


class TestRetryClassification:
    """§24 splits retryable from non-retryable; the job runner depends on it."""

    @pytest.mark.parametrize(
        "error",
        [
            ProviderRateLimitedError(),
            ProviderUnavailableError(),
            JobTimeoutError(),
            StorageOperationError(),
        ],
    )
    def test_transient_failures_are_retryable(self, error: AppError) -> None:
        assert error.retryable is True

    @pytest.mark.parametrize(
        "error",
        [
            ProviderRejectedError(),
            InsufficientCreditsError(),
            InvalidCredentialsError(),
            WorkspaceForbiddenError(),
            ClaimNotVerifiedError(),
        ],
    )
    def test_permanent_failures_are_not_retryable(self, error: AppError) -> None:
        """Retrying these burns quota and fails identically every time."""
        assert error.retryable is False


class TestMessageSafety:
    def test_invalid_credentials_does_not_reveal_which_field_was_wrong(self) -> None:
        """Distinguishing the two turns login into an account enumeration oracle."""
        message = InvalidCredentialsError().message.lower()
        assert "email" in message and "password" in message
        assert "not found" not in message
        assert "no such user" not in message

    def test_message_can_be_overridden_but_code_is_fixed(self) -> None:
        error = NotFoundError("Project not found.")
        assert error.message == "Project not found."
        assert error.code is ErrorCode.PRODUCT_NOT_FOUND

    def test_repr_shows_code_and_message_only(self) -> None:
        assert repr(NotFoundError("gone")) == (
            "NotFoundError(code='PRODUCT_NOT_FOUND', message='gone')"
        )

    def test_details_default_to_empty_not_none(self) -> None:
        assert NotFoundError().details == {}


class TestClaimNotVerified:
    """§13/§109 — the Truth Layer refusing to let inference become ad copy."""

    def test_it_is_a_conflict_not_a_validation_error(self) -> None:
        error = ClaimNotVerifiedError()
        assert error.http_status == 409
        assert error.code is ErrorCode.CLAIM_NOT_VERIFIED
