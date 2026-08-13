"""Balance, ledger and cost estimates (§95, PHASE 18).

Three endpoints, and the third is the one that matters to a user: an estimate
they see *before* pressing Generate (P18-T07). Spending a customer's credits
without telling them the number first is the sort of thing that produces
refunds rather than repeat business.

The estimate endpoint deliberately does not reserve anything. It is a quote,
and quoting is a read — a GET that took money would be a surprising GET.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from aipvs_api.dependencies import CurrentUser, OriginDep, SessionDep, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import AuditAction, CreditTransactionType, Permission
from backend_core.domain.models import CreditAccount, CreditTransaction
from backend_core.services.audit import AuditService
from backend_core.services.cost import CostEstimate, estimate_project
from backend_core.services.credit_ledger import LedgerCreditService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["credits"])


class CreditAccountResponse(BaseModel):
    balance: float = Field(description="Credits held, including reserved.")
    reserved: float = Field(description="The part already promised to jobs in flight.")
    available: float = Field(description="What a new job may be charged against.")
    lifetime_granted: float
    lifetime_spent: float

    @classmethod
    def of(cls, account: CreditAccount) -> CreditAccountResponse:
        return cls(
            balance=account.balance,
            reserved=account.reserved,
            available=account.available,
            lifetime_granted=account.lifetime_granted,
            lifetime_spent=account.lifetime_spent,
        )


class CreditTransactionResponse(BaseModel):
    id: uuid.UUID
    transaction_type: CreditTransactionType
    amount: float = Field(description="Always positive; the type says which way it moved.")
    balance_after: float
    reserved_after: float
    job_id: uuid.UUID | None
    description: str
    created_at: datetime

    @classmethod
    def of(cls, transaction: CreditTransaction) -> CreditTransactionResponse:
        return cls(
            id=transaction.id,
            transaction_type=transaction.transaction_type,
            amount=transaction.amount,
            balance_after=transaction.balance_after,
            reserved_after=transaction.reserved_after,
            job_id=transaction.job_id,
            description=transaction.description,
            created_at=transaction.created_at,
        )


class CostLine(BaseModel):
    label: str
    credits: float


class CostEstimateResponse(BaseModel):
    """A quote, and whether the workspace can cover it (P18-T07)."""

    expected: float = Field(description="What this is likely to cost.")
    maximum: float = Field(
        description=(
            "What will be reserved. Higher than `expected` because providers "
            "round up and a retry costs again."
        )
    )
    lines: list[CostLine]
    available: float
    affordable: bool = Field(
        description="Whether `maximum` fits in the available balance right now."
    )

    @classmethod
    def of(cls, estimate: CostEstimate, available: float) -> CostEstimateResponse:
        return cls(
            expected=estimate.expected,
            maximum=estimate.maximum,
            lines=[CostLine(label=label, credits=credits) for label, credits in estimate.lines],
            available=available,
            affordable=estimate.maximum <= available,
        )


class GrantRequest(ApiRequest):
    amount: float = Field(gt=0, le=1_000_000)
    idempotency_key: str = Field(
        min_length=8,
        max_length=160,
        description=(
            "Required. A grant has no natural id, so this key is the only thing "
            "between a retried purchase webhook and a doubled balance."
        ),
    )
    description: str = Field(default="", max_length=500)


@router.get(
    "/credits",
    response_model=CreditAccountResponse,
    summary="Credit balance",
    dependencies=[require_permission(Permission.WORKSPACE_READ)],
)
async def get_balance(workspace_id: uuid.UUID, session: SessionDep) -> CreditAccountResponse:
    """The workspace's balance (P18-T08).

    Creates the account on first read rather than 404ing. A workspace that has
    never generated anything has no account row, and "you have no account" is a
    worse answer than "you have your signup grant".
    """
    account = await LedgerCreditService(session).ensure_account(workspace_id)
    return CreditAccountResponse.of(account)


@router.get(
    "/credits/transactions",
    response_model=list[CreditTransactionResponse],
    summary="Credit history",
    dependencies=[require_permission(Permission.WORKSPACE_READ)],
)
async def list_transactions(
    workspace_id: uuid.UUID, session: SessionDep, limit: int = 100
) -> list[CreditTransactionResponse]:
    """The ledger, newest first (P18-T08).

    Every movement, including reservations that were released. A history that
    showed only charges would leave a user unable to explain why their
    available balance dipped during a generation that failed.
    """
    transactions = await LedgerCreditService(session).history(
        workspace_id=workspace_id, limit=min(max(limit, 1), 500)
    )
    return [CreditTransactionResponse.of(transaction) for transaction in transactions]


@router.get(
    "/projects/{project_id}/cost-estimate",
    response_model=CostEstimateResponse,
    summary="What generating this project will cost",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def estimate_cost(
    workspace_id: uuid.UUID, project_id: uuid.UUID, session: SessionDep
) -> CostEstimateResponse:
    """§95's pre-generation quote (P18-T07).

    Estimated from the approved storyboard's actual shot durations when one
    exists, and from the project's target duration when it does not — so the
    number a user sees before writing a storyboard is a rough guide, and the
    one they see before pressing Generate is derived from the shots that will
    actually be generated.

    Reserves nothing. This is a quote, and a GET that took money would be a
    surprising GET.
    """
    from backend_core.config import get_settings
    from backend_core.repositories.storyboards import StoryboardRepository
    from backend_core.services.projects import ProjectService

    project = await ProjectService(session).get(workspace_id=workspace_id, project_id=project_id)
    storyboard = await StoryboardRepository(session).approved(workspace_id, project_id)

    if storyboard is not None:
        shots = await StoryboardRepository(session).list_shots(workspace_id, storyboard.id)
        durations = [shot.duration_seconds for shot in shots]
        narration_seconds = sum(
            shot.duration_seconds for shot in shots if shot.voiceover_text.strip()
        )
    else:
        # No storyboard yet. A single slot of the project's whole duration is
        # the honest approximation: the cost scales with seconds generated, and
        # the shot count does not change the total.
        durations = [float(project.duration_seconds)]
        narration_seconds = float(project.duration_seconds)

    estimate = estimate_project(
        shot_durations=durations,
        quality=project.quality_mode,
        narration_seconds=narration_seconds,
        with_qc=get_settings().enable_qc,
    )
    account = await LedgerCreditService(session).ensure_account(workspace_id)
    return CostEstimateResponse.of(estimate, account.available)


@router.post(
    "/credits/grant",
    response_model=CreditAccountResponse,
    summary="Add credits",
    dependencies=[require_permission(Permission.BILLING_MANAGE)],
)
async def grant_credits(
    workspace_id: uuid.UUID,
    payload: GrantRequest,
    user: CurrentUser,
    session: SessionDep,
    origin: OriginDep,
) -> CreditAccountResponse:
    """Top up a workspace. `BILLING_MANAGE` — owners only (§40).

    Audited as a `CREDIT_ADJUSTMENT` (§60): moving money is the category of
    action a trail exists for.
    """
    ledger = LedgerCreditService(session)
    await ledger.grant(
        workspace_id=workspace_id,
        amount=payload.amount,
        idempotency_key=payload.idempotency_key,
        actor_user_id=user.id,
        description=payload.description,
    )
    await AuditService(session).record(
        AuditAction.CREDIT_ADJUSTMENT,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        target_type="credit_account",
        target_id=workspace_id,
        origin=origin,
        context={"amount": payload.amount, "reason": payload.description},
    )
    account = await ledger.get_account(workspace_id)
    return CreditAccountResponse.of(account)


__all__: list[str] = ["router"]
