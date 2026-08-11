"""Ambient correlation context (taskbook §63).

Every log line must carry enough identity to reconstruct what happened:
``request_id`` and ``trace_id`` for HTTP, plus ``user_id`` / ``workspace_id``
once authenticated; ``job_id`` / ``provider_job_id`` / ``project_id`` /
``shot_id`` for background work.

Threading those through every function signature would be intolerable, so they
live in ``ContextVar``s. That choice is safe here because contextvars are
per-task in asyncio — concurrent requests cannot see each other's values, which
a module-level global or a thread-local would not guarantee under async.

Bind with :func:`bind_context`, which returns a token for exact restoration.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Final

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_workspace_id: ContextVar[str | None] = ContextVar("workspace_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_provider_job_id: ContextVar[str | None] = ContextVar("provider_job_id", default=None)
_project_id: ContextVar[str | None] = ContextVar("project_id", default=None)
_shot_id: ContextVar[str | None] = ContextVar("shot_id", default=None)

_VARS: Final[dict[str, ContextVar[str | None]]] = {
    "request_id": _request_id,
    "trace_id": _trace_id,
    "user_id": _user_id,
    "workspace_id": _workspace_id,
    "job_id": _job_id,
    "provider_job_id": _provider_job_id,
    "project_id": _project_id,
    "shot_id": _shot_id,
}


def get_request_id() -> str | None:
    """The current request's id, if any."""
    return _request_id.get()


def get_context() -> dict[str, str]:
    """Every bound correlation field. Unset fields are omitted."""
    return {name: value for name, var in _VARS.items() if (value := var.get()) is not None}


def set_context(**fields: str | None) -> dict[str, Token[str | None]]:
    """Bind correlation fields, returning tokens for :func:`reset_context`.

    Unknown field names are ignored rather than raising: a caller adding
    context should never be able to break the request it is describing.
    """
    tokens: dict[str, Token[str | None]] = {}
    for name, value in fields.items():
        var = _VARS.get(name)
        if var is not None:
            tokens[name] = var.set(value)
    return tokens


def reset_context(tokens: dict[str, Token[str | None]]) -> None:
    """Restore the values captured before :func:`set_context`."""
    for name, token in tokens.items():
        var = _VARS.get(name)
        if var is not None:
            var.reset(token)


@contextmanager
def bind_context(**fields: str | None) -> Iterator[None]:
    """Bind correlation fields for the duration of a block.

    Restores previous values on the way out, including on exception, so a
    failed job cannot leak its ``job_id`` into whatever the worker does next.
    """
    tokens = set_context(**fields)
    try:
        yield
    finally:
        reset_context(tokens)


def context_as_log_fields() -> dict[str, Any]:
    """Correlation fields shaped for a log record's ``extra``."""
    return dict(get_context())
