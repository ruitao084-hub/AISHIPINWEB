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


#: §61's secure headers, applied to every response (P16-T10).
#:
#: This is a JSON API, so the set is smaller than a web page's and each entry
#: is here for a specific reason rather than by convention:
#:
#: - `nosniff` stops a browser deciding a JSON error body is HTML and running
#:   it. That is the actual XSS vector for an API — content sniffing, not
#:   markup we render, because we render none.
#: - `frame-ancestors 'none'` via CSP, plus the legacy `X-Frame-Options`, keeps
#:   API responses out of an attacker's iframe.
#: - `default-src 'none'` is correct for JSON: no response here should load
#:   anything at all, so the strictest policy is also the accurate one.
#: - `Referrer-Policy` keeps a URL containing ids out of a third party's logs.
#: - `Permissions-Policy` denies capabilities no API response needs, which
#:   matters for the `/docs` page in development.
#:
#: `Strict-Transport-Security` is *not* here unconditionally — it is added only
#: over HTTPS, because sending it over plain HTTP is meaningless and setting it
#: in local development would pin `localhost` to HTTPS in a developer's browser
#: for a year.
SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

#: Two years, with subdomains. Long because a short max-age is a downgrade
#: window, and this is only ever sent on a connection that is already HTTPS.
HSTS_VALUE: Final[str] = "max-age=63072000; includeSubDomains"

#: The interactive docs need to load their own JS and CSS, so the API-wide
#: `default-src 'none'` would render a blank page. They are disabled in
#: production (see `create_app`), so this relaxation only ever applies to a
#: development surface.
_DOCS_PATHS: Final[frozenset[str]] = frozenset({"/docs", "/redoc", "/openapi.json"})
_DOCS_CSP: Final[str] = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
    "https://cdn.jsdelivr.net; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach §61's secure headers to every response (P16-T10).

    In middleware rather than per route: a header that has to be remembered is
    a header that will be missing from the one endpoint nobody thought about.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            # `setdefault` semantics: a handler that deliberately set its own
            # policy keeps it. Nothing does today, and overriding one silently
            # would be worse than the duplication.
            if header not in response.headers:
                response.headers[header] = value

        if request.url.path in _DOCS_PATHS:
            response.headers["Content-Security-Policy"] = _DOCS_CSP

        # Only over TLS. `X-Forwarded-Proto` is honoured because the API runs
        # behind a proxy in every deployment that has HTTPS at all.
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        if request.url.scheme == "https" or forwarded == "https":
            response.headers["Strict-Transport-Security"] = HSTS_VALUE

        return response
