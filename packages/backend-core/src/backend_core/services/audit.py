"""Recording what happened (§60, P16-T14).

§60 lists the actions that must leave a trace. This module is the one way to
write one, so a new action is a call rather than a new table shape.

Two rules the API relies on:

**Recording never fails the action it records.** An audit write that raised
would turn a successful download into a 500, and losing one row is a smaller
harm than refusing work the user is entitled to. Failures are logged and
swallowed — with the record's content in the log line, so the trail survives in
the log stream even when the table write did not.

**Nothing here takes a request object.** The API pulls the address, the agent
and the request id and hands them over as values. Passing the request would let
this module read anything on it, and audit code that can read a password field
eventually does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import AuditAction
from backend_core.domain.models import AuditLog
from backend_core.observability import get_logger

logger = get_logger(__name__)

#: Keys that must never reach the context column, whatever a caller passes.
#: Belt and braces over §61: the call sites do not send these, and this makes
#: a future one that tries fail quietly rather than persist a secret.
_REDACTED: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "authorization",
    }
)


@dataclass(frozen=True, slots=True)
class RequestOrigin:
    """Where an action came from, as plain values (§60)."""

    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


class AuditService:
    """Appends to the audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        action: AuditAction,
        *,
        workspace_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        target_type: str = "",
        target_id: str | uuid.UUID = "",
        succeeded: bool = True,
        origin: RequestOrigin | None = None,
        context: dict[str, Any] | None = None,
    ) -> AuditLog | None:
        """Write one record. Returns `None` if the write failed.

        Not flushed here. The caller's transaction commits it alongside the
        action itself, which is what makes "the action happened but was not
        recorded" impossible for anything that succeeds.
        """
        origin = origin or RequestOrigin()
        entry = AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type[:64],
            target_id=str(target_id)[:64],
            succeeded=succeeded,
            ip_address=(origin.ip_address or None) and origin.ip_address[:45],
            user_agent=(origin.user_agent or None) and origin.user_agent[:512],
            request_id=(origin.request_id or None) and origin.request_id[:128],
            context=_scrub(context or {}),
        )

        try:
            self._session.add(entry)
        except Exception:
            # An audit write must not fail the action it records. The log line
            # carries the same facts, so the trail survives in the log stream.
            logger.exception(
                "audit_write_failed",
                extra={"action": action.value, "target_id": str(target_id)},
            )
            return None

        logger.info(
            "audit",
            extra={
                "action": action.value,
                "workspace_id": str(workspace_id) if workspace_id else None,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "target_type": target_type,
                "target_id": str(target_id),
                "succeeded": succeeded,
            },
        )
        return entry


def _scrub(context: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that looks like a credential, at any depth."""
    clean: dict[str, Any] = {}
    for key, value in context.items():
        if key.lower() in _REDACTED:
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = _scrub(value)
        else:
            clean[key] = value
    return clean


__all__ = ["AuditService", "RequestOrigin"]
