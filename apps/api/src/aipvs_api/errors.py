"""Exception handlers producing the uniform error envelope (taskbook §41).

Every failure leaves the API in exactly one shape::

    {"error": {"code": "PROJECT_NOT_FOUND", "message": "...", "request_id": "..."}}

That includes the ones a developer does not write by hand: FastAPI's 422
validation errors, Starlette's 404 and 405, and unhandled exceptions. If those
kept their framework defaults, a client would have to parse three different
error formats and would meet an error without a ``code``.

The other rule here is that an unexpected exception never reaches the client
verbatim. Tracebacks and driver messages carry table names, file paths and
connection strings with passwords (§63) — the details go to the log, the client
gets ``INTERNAL_ERROR`` plus the ``request_id`` that finds it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger, get_request_id

logger = get_logger(__name__)


class ErrorDetail(BaseModel):
    """The error object itself."""

    code: ErrorCode = Field(description="Stable machine-readable code (taskbook §65).")
    message: str = Field(description="Human-readable, safe to display to a user.")
    request_id: str | None = Field(
        default=None,
        description="Correlates this response with the server logs.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context. Never contains secrets.",
    )


class ErrorResponse(BaseModel):
    """Uniform error envelope returned by every failing endpoint."""

    error: ErrorDetail


def _request_id_for(request: Request) -> str | None:
    """Prefer the ambient context, fall back to request state.

    Exception handlers run outside the middleware's context manager, so the
    contextvar may already be unwound by the time we get here.
    """
    return get_request_id() or getattr(request.state, "request_id", None)


def _envelope(
    request: Request,
    *,
    code: ErrorCode,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=_request_id_for(request),
            details=details or None,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """Render a deliberate application error."""
    assert isinstance(exc, AppError)

    # 5xx means we broke; 4xx means the caller did. Only the former deserves
    # an error-level log and a stack trace.
    if exc.http_status >= 500:
        logger.error(
            "application error",
            extra={"error_code": exc.code.value, "retryable": exc.retryable},
            exc_info=exc,
        )
    else:
        logger.info(
            "client error",
            extra={"error_code": exc.code.value, "http_status": exc.http_status},
        )

    return _envelope(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.http_status,
        details=exc.details,
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI's request validation failure in our envelope.

    Pydantic's ``errors()`` echoes the offending input back. That is genuinely
    useful for debugging a form, and genuinely dangerous for a login body — so
    the location and reason are kept and the submitted value is dropped.
    """
    assert isinstance(exc, RequestValidationError)

    fields = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "reason": error.get("msg", "invalid"),
        }
        for error in exc.errors()
    ]

    return _envelope(
        request,
        code=ErrorCode.VALIDATION_ERROR,
        message="The request payload is invalid.",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        details={"fields": fields},
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette's own 404/405/etc. in our envelope."""
    assert isinstance(exc, StarletteHTTPException)

    code = {
        status.HTTP_401_UNAUTHORIZED: ErrorCode.AUTH_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN: ErrorCode.WORKSPACE_FORBIDDEN,
        status.HTTP_404_NOT_FOUND: ErrorCode.PRODUCT_NOT_FOUND,
    }.get(
        exc.status_code,
        ErrorCode.VALIDATION_ERROR if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR,
    )

    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return _envelope(request, code=code, message=detail, status_code=exc.status_code)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: something we did not anticipate.

    The client is told nothing beyond the request id — the traceback goes to
    the log, where it is correlated by that same id.
    """
    logger.exception(
        "unhandled exception",
        extra={"http_method": request.method, "http_path": request.url.path},
    )
    return _envelope(
        request,
        code=ErrorCode.INTERNAL_ERROR,
        message="An unexpected error occurred.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler so no failure path escapes the envelope."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
