"""Credit reservation, as an interface with a no-op default (§22, §18).

§22 is unusually specific about the sequencing here, and the reason is worth
stating: it wants `CreditService` **defined** from PHASE 9 but **implemented**
in PHASE 18, so the job orchestrator has the correct boundaries from its first
day without the unbuilt billing system blocking the video pipeline.

So this module is the shape, and `NoopCreditService` is the behaviour until
PHASE 18 replaces it. Reserve/capture/release calls are already in the right
places in the orchestrator; today they do nothing.

The three-step protocol is not decoration. Reserving before work starts is what
stops a workspace queueing a thousand jobs it cannot pay for; capturing only on
success is what stops a failed generation being billed; releasing on failure is
what stops reservations leaking until a workspace looks broke.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from backend_core.config import Settings, get_settings
from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class InsufficientCreditsError(AppError):
    """The workspace cannot cover this job."""

    code = ErrorCode.INSUFFICIENT_CREDITS
    http_status = 402
    default_message = "This workspace does not have enough credits for that."


@runtime_checkable
class CreditService(Protocol):
    """Reserve, capture and release credits around a job (§22).

    The three methods return `object` rather than `None` because the job
    orchestrator ignores what comes back, while the real ledger returns the
    resulting balance for the endpoints that show it. Pinning the protocol to
    `None` would force the ledger to hide that behind a second set of methods
    for no gain.
    """

    async def reserve(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> object:
        """Hold `amount` against the workspace, or refuse.

        Raises :class:`InsufficientCreditsError` — which §24 classifies as
        permanent, since retrying a job the workspace cannot afford will fail
        identically every time.
        """
        ...

    async def capture(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> object:
        """Convert a reservation into a charge, on success.

        `amount` is the *actual* cost, which may differ from the estimate —
        a provider that billed for four seconds of a five-second clip should
        not be charged as five.
        """
        ...

    async def release(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> object:
        """Give back an unused reservation, on failure or cancellation."""
        ...


class NoopCreditService:
    """Does nothing, loudly enough to be traceable (§22, PHASE 18).

    Logs at debug rather than staying silent: when PHASE 18 lands, "was reserve
    ever called for this job?" needs an answer, and the answer should not
    depend on someone having added logging first.
    """

    async def reserve(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        logger.debug(
            "credits_reserve_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id), "amount": amount},
        )

    async def capture(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        logger.debug(
            "credits_capture_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id), "amount": amount},
        )

    async def release(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> None:
        logger.debug(
            "credits_release_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id)},
        )


def get_credit_service(
    session: AsyncSession | None = None, settings: Settings | None = None
) -> CreditService:
    """The configured credit service (§22, PHASE 18).

    `ENABLE_CREDITS=false` yields the no-op, which is the MVP default. With the
    flag on, the real ledger is returned — and it needs the caller's session,
    because a reservation and the job it is for must commit together.

    Refusing to run without a session when credits are enabled is deliberate.
    Falling back to the no-op there would mean a workspace that turned metering
    *on* silently got none, and believing you are metered when you are not is
    worse than a startup error.
    """
    resolved = settings or get_settings()
    if not resolved.enable_credits:
        return NoopCreditService()

    if session is None:
        raise ValueError(
            "ENABLE_CREDITS is on, so the credit ledger needs a database session. "
            "Pass the caller's AsyncSession to get_credit_service()."
        )

    from backend_core.services.credit_ledger import LedgerCreditService

    return LedgerCreditService(session)


__all__ = [
    "CreditService",
    "InsufficientCreditsError",
    "NoopCreditService",
    "get_credit_service",
]
