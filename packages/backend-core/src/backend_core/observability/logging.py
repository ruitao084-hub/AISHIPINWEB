"""Structured JSON logging with secret redaction (taskbook §63).

Two requirements drive this module:

1. **Logs are JSON** and carry the correlation fields from
   :mod:`backend_core.observability.context`, so a request or job can be
   reconstructed from a log aggregator.
2. **Secrets never reach a log.** §63 forbids API keys, passwords, full
   ``Authorization`` headers and raw image base64. Redaction is applied here,
   at the formatter, rather than trusting every call site to remember — one
   forgotten ``logger.info(payload)`` is all it takes to leak a provider key
   into a log aggregator that a much wider group can read.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, Final

# Redaction is by *key name*, because that is what survives nesting: a value is
# only recognisable as a secret by the field it sits in.
_SENSITIVE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization"
    r"|auth|credential|private[_-]?key|session|cookie|jwt|signature|bearer)",
    re.IGNORECASE,
)

# A connection string with inline credentials, e.g.
# postgresql+psycopg://user:hunter2@host:5432/db — the password must go even
# when the whole URL was logged as an ordinary string.
_URL_CREDENTIALS: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/\s@]+):(?P<pw>[^@/\s]+)@"
)

# Long unbroken base64-ish runs are almost always embedded media (§63) — keep
# the shape for debugging, drop the payload.
_LONG_BLOB: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9+/=]{256,}")

REDACTED: Final[str] = "[REDACTED]"

_MAX_DEPTH: Final[int] = 6

# Attributes the stdlib puts on every record; anything else a caller passed via
# `extra` is application context worth emitting.
_STANDARD_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "thread",
        "threadName",
        "taskName",
    }
)


def redact_text(value: str) -> str:
    """Strip credentials and huge blobs from a free-text string."""
    cleaned = _URL_CREDENTIALS.sub(rf"\g<scheme>\g<user>:{REDACTED}@", value)
    return _LONG_BLOB.sub("[REDACTED_BLOB]", cleaned)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets from an arbitrary logged value.

    Depth is capped so a self-referential structure cannot hang the logger —
    logging must never be able to take down the process it is describing.
    """
    if _depth > _MAX_DEPTH:
        return "[REDACTED_DEPTH]"

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY_PATTERN.search(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact(item, _depth + 1)
        return redacted
    if isinstance(value, list | tuple | set):
        return [redact(item, _depth + 1) for item in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        from backend_core.observability.context import context_as_log_fields

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        # Correlation fields (§63). Explicit `extra` wins over ambient context.
        payload.update(context_as_log_fields())

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = redact(value)

        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact_text(self.formatStack(record.stack_info))

        # `default=str` keeps UUIDs, datetimes and Decimals loggable rather
        # than raising inside the logger.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, force: bool = True) -> None:
    """Install the JSON formatter on the root logger.

    Called once at process start by the API, the worker and the render worker.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    if force:
        for existing in list(root.handlers):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; let records propagate to root so
    # access and error logs are JSON too rather than a second, plainer format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Thin wrapper so call sites import from one place."""
    return logging.getLogger(name)
