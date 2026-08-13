"""The real credit ledger (§95, PHASE 18).

§95's acceptance is three prohibitions, and this module is organised around
which mechanism makes each one true — because "the code is careful" is not one
of the three.

**No double charge.** Every reserve, capture and release derives its
idempotency key from `(job_id, type)`, and `credit_transactions` has a unique
constraint on `(workspace_id, idempotency_key)`. A worker redelivered under
`task_acks_late` writing the same capture twice violates a constraint instead
of billing twice. The second write is *absorbed*, not raised: the caller asked
for a state that already holds.

**No negative balance.** `SELECT ... FOR UPDATE` on the account row serialises
concurrent reservations, and three CHECK constraints (`balance >= 0`,
`reserved >= 0`, `reserved <= balance`) mean the database refuses an overdraft
even if this code's arithmetic is wrong. The lock is what makes two simultaneous
reservations of the last credit resolve to one success and one refusal, rather
than two successes and a negative balance.

**No charge on failure.** `release` is called from `JobService.fail` and
`JobService.cancel`, and it moves the reservation back without touching
`balance`. A capture that never happens costs nothing.

**Why the interface is async.** PHASE 9 defined `CreditService` synchronously,
when the implementation did nothing. A real ledger writes rows inside the
caller's transaction — which is exactly what makes reserve-and-enqueue atomic —
and that requires the `AsyncSession` the caller already holds. Keeping the sync
signature would have forced either a second connection (breaking atomicity) or
a queue of deferred writes (breaking the ordering §22 specifies).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import CreditTransactionType
from backend_core.domain.models import CreditAccount, CreditTransaction
from backend_core.errors import NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.services.credits import InsufficientCreditsError

logger = get_logger(__name__)

#: Credits a new workspace starts with, so a trial can produce a video without
#: a billing conversation. Generous enough for a few short videos and small
#: enough that automated signups are not a free rendering farm.
SIGNUP_GRANT: Final[float] = 100.0

#: Amounts are rounded to this many places before they are stored. Floats
#: accumulate error, and a balance that reads `19.999999999999996` makes every
#: comparison in a support conversation harder than it needs to be.
_PRECISION: Final[int] = 4


def _round(value: float) -> float:
    return round(value, _PRECISION)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """What one movement did, for a caller that wants to report it."""

    transaction_id: uuid.UUID | None
    amount: float
    balance: float
    reserved: float
    available: float
    #: True when this call found the work already done (§23). Not an error —
    #: the caller asked for a state that already holds.
    already_applied: bool = False


class LedgerCreditService:
    """§22's three-step protocol, backed by an append-only ledger.

    Holds the caller's session rather than opening its own, so a reservation
    and the job it is for commit together or not at all. A ledger on a separate
    connection would let a job exist that was never paid for, or a reservation
    exist for a job that was rolled back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- accounts (P18-T01) -------------------------------------------------

    async def ensure_account(self, workspace_id: uuid.UUID) -> CreditAccount:
        """The workspace's account, created with its signup grant if absent.

        Created lazily rather than at signup, because a workspace that never
        generates anything never needs one — and because a missing account
        found here is recoverable, where a missing account found during a
        reservation would fail a job for a bookkeeping reason.
        """
        account = await self._locked_account(workspace_id, for_update=False)
        if account is not None:
            return account

        account = CreditAccount(
            workspace_id=workspace_id,
            balance=SIGNUP_GRANT,
            reserved=0.0,
            lifetime_granted=SIGNUP_GRANT,
        )
        self._session.add(account)

        try:
            await self._session.flush()
        except IntegrityError:
            # Two first-jobs for a new workspace arriving together. The unique
            # constraint refused the second, which is correct — take the
            # winner rather than surfacing a 500 for a race nobody caused.
            await self._session.rollback()
            existing = await self._locked_account(workspace_id, for_update=False)
            if existing is None:
                raise
            return existing

        self._session.add(
            CreditTransaction(
                workspace_id=workspace_id,
                account_id=account.id,
                transaction_type=CreditTransactionType.GRANT,
                amount=SIGNUP_GRANT,
                balance_after=SIGNUP_GRANT,
                reserved_after=0.0,
                idempotency_key=f"signup:{workspace_id}",
                description="Signup grant",
            )
        )
        await self._session.flush()
        logger.info("credit_account_created", extra={"workspace_id": str(workspace_id)})
        return account

    async def get_account(self, workspace_id: uuid.UUID) -> CreditAccount:
        account = await self._locked_account(workspace_id, for_update=False)
        if account is None:
            raise NotFoundError(
                "This workspace has no credit account.",
                details={"workspace_id": str(workspace_id)},
            )
        return account

    async def _locked_account(
        self, workspace_id: uuid.UUID, *, for_update: bool
    ) -> CreditAccount | None:
        """Load the account, optionally taking a row lock.

        `FOR UPDATE` is what serialises concurrent reservations. Without it,
        two requests both read a balance of 10, both decide a cost of 8 fits,
        and both write — leaving `reserved` at 16 against a balance of 10. The
        CHECK constraint would then refuse the second write, which is safe but
        produces a 500 rather than a clear "not enough credits".
        """
        query = select(CreditAccount).where(CreditAccount.workspace_id == workspace_id)
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def _existing(
        self, workspace_id: uuid.UUID, idempotency_key: str
    ) -> CreditTransaction | None:
        result = await self._session.execute(
            select(CreditTransaction).where(
                CreditTransaction.workspace_id == workspace_id,
                CreditTransaction.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    # -- the three steps (§22, P18-T03/T04/T05) -----------------------------

    async def reserve(
        self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float
    ) -> LedgerEntry:
        """Hold `amount`, or refuse (P18-T03).

        Refuses with `InsufficientCreditsError`, which §24 classifies as
        permanent — retrying a job a workspace cannot afford fails identically
        every time, and a retry loop on it would just burn queue capacity.
        """
        amount = _round(max(0.0, amount))
        key = f"reserve:{job_id}"

        prior = await self._existing(workspace_id, key)
        if prior is not None:
            account = await self.get_account(workspace_id)
            return _entry(prior, account, already_applied=True)

        await self.ensure_account(workspace_id)
        locked = await self._locked_account(workspace_id, for_update=True)
        if locked is None:  # pragma: no cover — ensure_account just made one
            raise NotFoundError("This workspace has no credit account.")
        account = locked

        if amount > account.available:
            logger.info(
                "credits_insufficient",
                extra={
                    "workspace_id": str(workspace_id),
                    "job_id": str(job_id),
                    "requested": amount,
                    "available": account.available,
                },
            )
            raise InsufficientCreditsError(
                "This workspace does not have enough credits for that.",
                details={"required": amount, "available": account.available},
            )

        account.reserved = _round(account.reserved + amount)
        transaction = self._record(
            account,
            CreditTransactionType.RESERVE,
            amount=amount,
            job_id=job_id,
            key=key,
            description=f"Reserved for job {job_id}",
        )
        await self._session.flush()
        return _entry(transaction, account)

    async def capture(
        self, *, workspace_id: uuid.UUID, job_id: uuid.UUID, amount: float
    ) -> LedgerEntry:
        """Turn a reservation into a charge (P18-T04).

        `amount` is the *actual* cost and may differ from the estimate. Two
        cases, both real:

        - **Under the estimate.** The difference returns to the balance. A
          provider that billed four seconds of a five-second clip should not
          be charged as five.
        - **Over the estimate.** The excess is charged anyway, and can take the
          available balance to zero but never below — the work has already been
          done and the money already spent with the provider. Refusing here
          would leave the platform out of pocket for a video it delivered.
        """
        amount = _round(max(0.0, amount))
        key = f"capture:{job_id}"

        prior = await self._existing(workspace_id, key)
        if prior is not None:
            # A redelivered worker completing the same job twice. Absorbed
            # rather than raised: this is exactly what §23's idempotency is
            # for, and it is the single most important line in this module for
            # "no double charge".
            account = await self.get_account(workspace_id)
            return _entry(prior, account, already_applied=True)

        locked = await self._locked_account(workspace_id, for_update=True)
        if locked is None:
            raise NotFoundError("This workspace has no credit account.")
        account = locked

        held = await self._reserved_for(workspace_id, job_id)
        # Release the hold first, then charge. Doing it in this order means a
        # capture larger than its reservation is possible without the
        # `reserved <= balance` constraint tripping mid-update.
        account.reserved = _round(max(0.0, account.reserved - held))
        charge = min(amount, account.balance)
        account.balance = _round(account.balance - charge)
        account.lifetime_spent = _round(account.lifetime_spent + charge)

        transaction = self._record(
            account,
            CreditTransactionType.CAPTURE,
            amount=charge,
            job_id=job_id,
            key=key,
            description=f"Charged for job {job_id}",
        )
        await self._session.flush()

        if charge < amount:
            # The workspace owes more than it had. Recorded because it is a
            # real event with a billing consequence, not silently rounded away.
            logger.warning(
                "credits_capture_exceeded_balance",
                extra={
                    "workspace_id": str(workspace_id),
                    "job_id": str(job_id),
                    "owed": amount,
                    "charged": charge,
                },
            )
        return _entry(transaction, account)

    async def release(self, *, workspace_id: uuid.UUID, job_id: uuid.UUID) -> LedgerEntry:
        """Give back an unused reservation (P18-T05).

        Called on failure and on cancellation. Silently does nothing when there
        was no reservation, or when this job was already captured: both mean
        there is no hold to return, and raising would turn a clean failure path
        into a second failure.
        """
        key = f"release:{job_id}"

        prior = await self._existing(workspace_id, key)
        if prior is not None:
            account = await self.get_account(workspace_id)
            return _entry(prior, account, already_applied=True)

        locked = await self._locked_account(workspace_id, for_update=True)
        if locked is None:
            # Nothing to give back. A job that failed before its workspace ever
            # had an account is a clean no-op, not an error on a failure path.
            return LedgerEntry(
                transaction_id=None, amount=0.0, balance=0.0, reserved=0.0, available=0.0
            )
        account = locked

        held = await self._reserved_for(workspace_id, job_id)
        if held <= 0:
            return _entry(None, account)

        account.reserved = _round(max(0.0, account.reserved - held))
        transaction = self._record(
            account,
            CreditTransactionType.RELEASE,
            amount=held,
            job_id=job_id,
            key=key,
            description=f"Released reservation for job {job_id}",
        )
        await self._session.flush()
        return _entry(transaction, account)

    async def _reserved_for(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> float:
        """How much this job still holds.

        Read from the ledger rather than tracked on the job, so the answer
        comes from the same rows the balance does. A number kept in two places
        is a number that will eventually disagree with itself.
        """
        reserve = await self._existing(workspace_id, f"reserve:{job_id}")
        if reserve is None:
            return 0.0
        for settled in ("capture", "release"):
            if await self._existing(workspace_id, f"{settled}:{job_id}") is not None:
                return 0.0
        return _round(reserve.amount)

    # -- administration -----------------------------------------------------

    async def grant(
        self,
        *,
        workspace_id: uuid.UUID,
        amount: float,
        idempotency_key: str,
        actor_user_id: uuid.UUID | None = None,
        description: str = "",
    ) -> LedgerEntry:
        """Add credits (P18-T01).

        `idempotency_key` is required rather than derived, because a grant has
        no natural id: a top-up is whatever the caller says it is, and the only
        thing standing between a retried purchase webhook and a doubled balance
        is the key the caller chose.
        """
        if amount <= 0:
            raise ValidationError("A grant must be a positive amount.")

        prior = await self._existing(workspace_id, idempotency_key)
        if prior is not None:
            account = await self.get_account(workspace_id)
            return _entry(prior, account, already_applied=True)

        await self.ensure_account(workspace_id)
        locked = await self._locked_account(workspace_id, for_update=True)
        if locked is None:  # pragma: no cover
            raise NotFoundError("This workspace has no credit account.")
        account = locked

        amount = _round(amount)
        account.balance = _round(account.balance + amount)
        account.lifetime_granted = _round(account.lifetime_granted + amount)

        transaction = self._record(
            account,
            CreditTransactionType.GRANT,
            amount=amount,
            job_id=None,
            key=idempotency_key,
            description=description or "Credit grant",
            actor_user_id=actor_user_id,
        )
        await self._session.flush()
        logger.info(
            "credits_granted",
            extra={"workspace_id": str(workspace_id), "amount": amount},
        )
        return _entry(transaction, account)

    async def history(
        self, *, workspace_id: uuid.UUID, limit: int = 100
    ) -> list[CreditTransaction]:
        """The ledger, newest first (P18-T08)."""
        result = await self._session.execute(
            select(CreditTransaction)
            .where(CreditTransaction.workspace_id == workspace_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    def _record(
        self,
        account: CreditAccount,
        transaction_type: CreditTransactionType,
        *,
        amount: float,
        job_id: uuid.UUID | None,
        key: str,
        description: str,
        actor_user_id: uuid.UUID | None = None,
    ) -> CreditTransaction:
        transaction = CreditTransaction(
            workspace_id=account.workspace_id,
            account_id=account.id,
            transaction_type=transaction_type,
            amount=_round(amount),
            balance_after=account.balance,
            reserved_after=account.reserved,
            job_id=job_id,
            actor_user_id=actor_user_id,
            idempotency_key=key,
            description=description[:500],
        )
        self._session.add(transaction)
        return transaction


def _entry(
    transaction: CreditTransaction | None,
    account: CreditAccount,
    *,
    already_applied: bool = False,
) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=transaction.id if transaction is not None else None,
        amount=transaction.amount if transaction is not None else 0.0,
        balance=account.balance,
        reserved=account.reserved,
        available=account.available,
        already_applied=already_applied,
    )


__all__ = ["SIGNUP_GRANT", "LedgerCreditService", "LedgerEntry"]
