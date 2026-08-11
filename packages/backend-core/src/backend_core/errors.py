"""Error taxonomy and the application exception hierarchy (taskbook §65).

Error codes are a **closed set**. They are part of the API contract: clients
switch on them to decide whether to retry, re-authenticate, top up credits or
show a message. An ad-hoc string invented at a call site breaks that contract
silently, so ``ErrorCode`` is an enum and adding a member is a deliberate act.

Every error carries three separable things:

* ``code`` — stable, machine-readable, safe to expose.
* ``message`` — human-readable and **safe to show a user**. Never build it
  from an upstream exception: provider responses and connection strings carry
  credentials (§63, §166).
* ``details`` — optional structured context, also user-safe.

Anything unsafe belongs in the log, correlated by ``request_id``, not in the
response.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """The complete set of error codes (§65)."""

    # Auth and access
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_UNAUTHORIZED = "AUTH_UNAUTHORIZED"
    WORKSPACE_FORBIDDEN = "WORKSPACE_FORBIDDEN"

    # Resources
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    ASSET_INVALID = "ASSET_INVALID"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"

    # Product truth (§13)
    CLAIM_NOT_VERIFIED = "CLAIM_NOT_VERIFIED"

    # Workflow
    PROJECT_INVALID_STATE = "PROJECT_INVALID_STATE"

    # Billing
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"

    # Providers
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"

    # Jobs
    JOB_TIMEOUT = "JOB_TIMEOUT"
    JOB_CANCELED = "JOB_CANCELED"

    # Pipeline
    RENDER_FAILED = "RENDER_FAILED"
    QC_FAILED = "QC_FAILED"

    # Infrastructure
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Generic input validation. Not in the §65 list because that list covers
    # domain failures; FastAPI's own 422 still needs a code in the envelope so
    # clients never see an error without one.
    VALIDATION_ERROR = "VALIDATION_ERROR"


class AppError(Exception):
    """Base class for every deliberate application failure.

    ``retryable`` classifies the error for §24's retry policy. It belongs on
    the error rather than in the retry loop: whether a 429 is worth retrying is
    a property of what went wrong, not of who is handling it. The policy
    (backoff, jitter, attempt cap) lands with the job system in PHASE 9.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    retryable: bool = False
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"


# --- Auth and access -------------------------------------------------------


class InvalidCredentialsError(AppError):
    code = ErrorCode.AUTH_INVALID_CREDENTIALS
    http_status = 401
    # Deliberately does not distinguish "no such user" from "wrong password":
    # doing so turns the login endpoint into an account enumeration oracle.
    default_message = "Incorrect email or password."


class UnauthorizedError(AppError):
    code = ErrorCode.AUTH_UNAUTHORIZED
    http_status = 401
    default_message = "Authentication required."


class WorkspaceForbiddenError(AppError):
    code = ErrorCode.WORKSPACE_FORBIDDEN
    http_status = 403
    default_message = "You do not have access to this workspace."


# --- Resources -------------------------------------------------------------


class NotFoundError(AppError):
    """A resource does not exist, or the caller may not know that it does.

    Returning 404 rather than 403 for another workspace's resource is
    intentional: a 403 confirms the id exists, which leaks across tenants.
    """

    code = ErrorCode.PRODUCT_NOT_FOUND
    http_status = 404
    default_message = "Resource not found."


class AssetInvalidError(AppError):
    code = ErrorCode.ASSET_INVALID
    http_status = 400
    default_message = "The uploaded file is not valid."


class UploadTooLargeError(AppError):
    code = ErrorCode.UPLOAD_TOO_LARGE
    http_status = 413
    default_message = "The uploaded file exceeds the size limit."


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 422
    default_message = "The request payload is invalid."


# --- Product truth (§13, §109) ---------------------------------------------


class ClaimNotVerifiedError(AppError):
    """A generation step tried to use an unverified claim.

    This is the Truth Layer refusing to let an AI inference become advertising
    copy. It is a bug in the caller, not a user error.
    """

    code = ErrorCode.CLAIM_NOT_VERIFIED
    http_status = 409
    default_message = "This claim has not been verified and cannot be used in a script."


# --- Workflow --------------------------------------------------------------


class ProjectInvalidStateError(AppError):
    code = ErrorCode.PROJECT_INVALID_STATE
    http_status = 409
    default_message = "The project is not in a state that allows this action."


# --- Billing ---------------------------------------------------------------


class InsufficientCreditsError(AppError):
    code = ErrorCode.INSUFFICIENT_CREDITS
    http_status = 402
    # Not retryable: retrying without a top-up fails identically (§24).
    retryable = False
    default_message = "Insufficient credits for this operation."


# --- Providers -------------------------------------------------------------


class ProviderUnavailableError(AppError):
    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = 503
    retryable = True
    default_message = "The generation provider is temporarily unavailable."


class ProviderRateLimitedError(AppError):
    code = ErrorCode.PROVIDER_RATE_LIMITED
    http_status = 429
    retryable = True
    default_message = "The generation provider is rate limiting requests."


class ProviderRejectedError(AppError):
    """The provider refused the request on its merits.

    Not retryable: a policy violation or an illegal parameter fails the same
    way every time, and retrying burns quota for nothing (§24).
    """

    code = ErrorCode.PROVIDER_REJECTED
    http_status = 422
    retryable = False
    default_message = "The generation provider rejected this request."


# --- Jobs ------------------------------------------------------------------


class JobTimeoutError(AppError):
    code = ErrorCode.JOB_TIMEOUT
    http_status = 504
    retryable = True
    default_message = "The job did not complete within its time limit."


class JobCanceledError(AppError):
    code = ErrorCode.JOB_CANCELED
    http_status = 409
    default_message = "The job was canceled."


# --- Pipeline --------------------------------------------------------------


class RenderFailedError(AppError):
    code = ErrorCode.RENDER_FAILED
    http_status = 500
    retryable = True
    default_message = "Rendering failed."


class QualityCheckFailedError(AppError):
    code = ErrorCode.QC_FAILED
    http_status = 422
    default_message = "The output did not pass quality checks."


# --- Infrastructure --------------------------------------------------------


class StorageOperationError(AppError):
    code = ErrorCode.STORAGE_ERROR
    http_status = 500
    retryable = True
    default_message = "A storage operation failed."


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    http_status = 500
    default_message = "An unexpected error occurred."
