"""HTTP middleware: request correlation and access logging (taskbook §63)."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend_core.observability import bind_context, get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"

# An inbound id is echoed into every log line, so it is untrusted input.
# Restricting it to a short token blocks newline injection — which would
# otherwise let a caller forge extra lines in a JSON log stream — and stops an
# oversized header from bloating every record.
_SAFE_REQUEST_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def _resolve_request_id(request: Request) -> str:
    """Reuse a caller-supplied id when it is safe, otherwise mint one.

    Honouring the inbound header lets a trace span the web app and the API;
    rejecting malformed values keeps the log stream trustworthy.
    """
    candidate = request.headers.get(REQUEST_ID_HEADER)
    if candidate and _SAFE_REQUEST_ID.match(candidate):
        return candidate
    return str(uuid.uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind correlation ids for the request and log its outcome.

    The id is also written to ``request.state`` so exception handlers — which
    run outside this middleware's context — can put it in the error envelope
    (§41), giving a user something to quote in a support request that maps
    straight to the server logs.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _resolve_request_id(request)
        request.state.request_id = request_id

        started = time.perf_counter()
        with bind_context(request_id=request_id, trace_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                # Log here and re-raise: the exception handlers own the
                # response shape, this owns the record that it happened.
                logger.exception(
                    "request failed",
                    extra={
                        "http_method": request.method,
                        "http_path": request.url.path,
                        "duration_ms": duration_ms,
                    },
                )
                raise

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request completed",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
