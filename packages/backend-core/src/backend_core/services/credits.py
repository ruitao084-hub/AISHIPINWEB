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
from typing import Protocol, runtime_checkable

from backend_core.config import Settings, get_settings
from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger

logger = get_logger(__name__)


class InsufficientCreditsError(AppError):
    """The workspace cannot cover this job."""

    code = ErrorCode.INSUFFICIENT_CREDITS
    http_status = 402
    default_message = "This workspace does not have enough credits for that."


@runtime_checkable
class CreditService(Protocol):
    """Reserve, capture and release credits around a job (§22)."""

    def reserve(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        """Hold `amount` against the workspace, or refuse.

        Raises :class:`InsufficientCreditsError` — which §24 classifies as
        permanent, since retrying a job the workspace cannot afford will fail
        identically every time.
        """
        ...

    def capture(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        """Convert a reservation into a charge, on success.

        `amount` is the *actual* cost, which may differ from the estimate —
        a provider that billed for four seconds of a five-second clip should
        not be charged as five.
        """
        ...

    def release(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Give back an unused reservation, on failure or cancellation."""
        ...


class NoopCreditService:
    """Does nothing, loudly enough to be traceable (§22, PHASE 18).

    Logs at debug rather than staying silent: when PHASE 18 lands, "was reserve
    ever called for this job?" needs an answer, and the answer should not
    depend on someone having added logging first.
    """

    def reserve(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        logger.debug(
            "credits_reserve_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id), "amount": amount},
        )

    def capture(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float) -> None:
        logger.debug(
            "credits_capture_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id), "amount": amount},
        )

    def release(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> None:
        logger.debug(
            "credits_release_noop",
            extra={"workspace_id": str(workspace_id), "job_id": str(job_id)},
        )


def get_credit_service(settings: Settings | None = None) -> CreditService:
    """The configured credit service.

    `ENABLE_CREDITS=false` yields the no-op, which is the MVP default (§22).
    PHASE 18 adds the real ledger behind the same flag.
    """
    resolved = settings or get_settings()
    if not resolved.enable_credits:
        return NoopCreditService()

    # PHASE 18 replaces this branch with the real ledger. Failing loudly rather
    # than falling back to the no-op: a workspace that turned credits *on* and
    # silently got no enforcement would believe it was being metered when it
    # was not, which is worse than an error at boot.
    raise NotImplementedError(
        "ENABLE_CREDITS is on but the credit ledger has not been built yet (PHASE 18). "
        "Set ENABLE_CREDITS=false to run without credit enforcement."
    )


__all__ = [
    "CreditService",
    "InsufficientCreditsError",
    "NoopCreditService",
    "get_credit_service",
]
