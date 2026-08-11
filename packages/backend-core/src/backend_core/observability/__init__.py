"""Observability — structured logging and correlation context (§63, §64).

Every log line is JSON and carries the ids needed to reconstruct a request or
a job. Secrets are redacted at the formatter, not at call sites, because one
forgotten payload log is enough to leak a provider key.

Metrics (§64) land with the systems they measure: job counters in PHASE 9,
render and provider latency in PHASE 13, cost in PHASE 18.
"""

from backend_core.observability.context import (
    bind_context,
    context_as_log_fields,
    get_context,
    get_request_id,
    reset_context,
    set_context,
)
from backend_core.observability.logging import (
    JsonFormatter,
    configure_logging,
    get_logger,
    redact,
    redact_text,
)

__all__ = [
    "JsonFormatter",
    "bind_context",
    "configure_logging",
    "context_as_log_fields",
    "get_context",
    "get_logger",
    "get_request_id",
    "redact",
    "redact_text",
    "reset_context",
    "set_context",
]
