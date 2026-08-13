"""Operator endpoints: providers, jobs and analytics (§55, §99, PHASE 19/22).

Everything here is global rather than per-workspace, and that is the reason it
is a separate router with a separate guard. A provider outage is not a tenant's
problem to configure around, and job monitoring across tenants is exactly the
data a workspace-scoped route must never return.

**The permission model.** `require_platform_admin` is deliberately not one of
§38's workspace permissions: those are about a role *inside* a workspace, and
no role inside one should confer the ability to read another's jobs. Platform
admin is a flag on the user, checked here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, params
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from aipvs_api.dependencies import CurrentUser, OriginDep, SessionDep
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    AuditAction,
    CreditTransactionType,
    JobStatus,
    JobType,
)
from backend_core.domain.models import (
    CreditTransaction,
    GenerationJob,
    ProviderConfig,
    User,
    Workspace,
)
from backend_core.errors import WorkspaceForbiddenError
from backend_core.providers.capabilities import VideoCapability, capability_matrix
from backend_core.services.audit import AuditService
from backend_core.services.router import ProviderRouter

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_platform_admin(user: CurrentUser) -> User:
    """Platform staff only (§99, P22-T01).

    Not a workspace permission. `is_platform_admin` is a column on the user
    because the question "may this person see every tenant's jobs" has nothing
    to do with their role in any one of them — and a workspace OWNER must not
    be able to answer it yes for themselves.
    """
    if not user.is_platform_admin:
        # 403 rather than 404: the admin surface's existence is not a secret,
        # and pretending it is would make a misconfigured account impossible
        # to diagnose.
        raise WorkspaceForbiddenError("This endpoint is restricted to platform staff.")
    return user


AdminUser = Annotated[User, Depends(require_platform_admin)]


def _admin_only() -> params.Depends:
    return params.Depends(dependency=require_platform_admin)


# --- providers (§54, §55, P19-T07) -----------------------------------------


class CapabilityResponse(BaseModel):
    """§140's schema, as the admin sees it (P19-T02)."""

    provider: str
    model: str
    text_to_video: bool
    image_to_video: bool
    reference_images: bool
    max_reference_images: int
    durations: list[float] = Field(
        description="Exact lengths offered. Empty means continuous up to the maximum."
    )
    max_duration_seconds: float
    ratios: list[str]
    resolutions: list[str]
    audio: bool
    cancel: bool
    webhook: bool
    cost_per_second: float
    typical_latency_seconds: float
    quality_modes: list[str]

    @classmethod
    def of(cls, capability: VideoCapability) -> CapabilityResponse:
        return cls(
            provider=capability.provider,
            model=capability.model,
            text_to_video=capability.text_to_video,
            image_to_video=capability.image_to_video,
            reference_images=capability.reference_images,
            max_reference_images=capability.max_reference_images,
            durations=list(capability.durations),
            max_duration_seconds=capability.max_duration_seconds,
            ratios=[ratio.value for ratio in capability.ratios],
            resolutions=list(capability.resolutions),
            audio=capability.audio,
            cancel=capability.cancel,
            webhook=capability.webhook,
            cost_per_second=capability.cost_per_second,
            typical_latency_seconds=capability.typical_latency_seconds,
            quality_modes=[mode.value for mode in capability.quality_modes],
        )


class ProviderConfigResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model: str
    kind: str
    enabled: bool
    priority: int
    cost_per_second: float | None
    max_concurrency: int | None
    circuit_open_until: datetime | None = Field(
        description="Set by the breaker, not by an operator. Null means closed."
    )
    circuit_trip_count: int
    notes: str | None

    # Observed behaviour, filled in by the health endpoint.
    attempts: int = 0
    failures: int = 0
    failure_rate: float = 0.0
    consecutive_failures: int = 0

    @classmethod
    def of(cls, config: ProviderConfig) -> ProviderConfigResponse:
        return cls(
            id=config.id,
            provider=config.provider,
            model=config.model,
            kind=config.kind,
            enabled=config.enabled,
            priority=config.priority,
            cost_per_second=config.cost_per_second,
            max_concurrency=config.max_concurrency,
            circuit_open_until=config.circuit_open_until,
            circuit_trip_count=config.circuit_trip_count,
            notes=config.notes,
        )


class SetProviderEnabledRequest(ApiRequest):
    enabled: bool
    notes: str | None = Field(default=None, max_length=2000)


@router.get(
    "/providers/capabilities",
    response_model=list[CapabilityResponse],
    summary="The capability matrix",
    dependencies=[_admin_only()],
)
async def list_capabilities() -> list[CapabilityResponse]:
    """§140's matrix (P19-T02).

    Read from code rather than the database: capabilities change when a vendor
    ships a model, and a row an operator could edit would let someone claim a
    provider does 7-second clips when it does not.
    """
    return [CapabilityResponse.of(entry) for entry in capability_matrix()]


@router.get(
    "/providers",
    response_model=list[ProviderConfigResponse],
    summary="Provider configuration and health",
    dependencies=[_admin_only()],
)
async def list_providers(
    session: SessionDep, kind: str | None = None
) -> list[ProviderConfigResponse]:
    """§54's config with §55's observed health folded in (P19-T04, P22-T05).

    One endpoint rather than two, because the only question anyone asks here is
    "which of these is working" — and answering it from two screens means
    reading a config that says enabled next to a health page that says failing.
    """
    provider_router = ProviderRouter(session)
    await provider_router.ensure_defaults()
    configs = await provider_router.list_configs(kind=kind)

    responses: list[ProviderConfigResponse] = []
    for config in configs:
        health = await provider_router.health(config.provider)
        response = ProviderConfigResponse.of(config)
        responses.append(
            response.model_copy(
                update={
                    "attempts": health.attempts,
                    "failures": health.failures,
                    "failure_rate": round(health.failure_rate, 4),
                    "consecutive_failures": health.consecutive_failures,
                }
            )
        )
    return responses


@router.post(
    "/providers/{provider}/enabled",
    response_model=ProviderConfigResponse,
    summary="Enable or disable a provider",
    dependencies=[_admin_only()],
)
async def set_provider_enabled(
    provider: str,
    payload: SetProviderEnabledRequest,
    user: AdminUser,
    session: SessionDep,
    origin: OriginDep,
    kind: str = "video",
) -> ProviderConfigResponse:
    """§54's switch (P19-T07).

    Enabling also clears the breaker: someone turning a provider back on means
    "try this again now", and leaving a breaker open would make the switch
    appear to do nothing.
    """
    config = await ProviderRouter(session).set_enabled(
        provider, enabled=payload.enabled, kind=kind, notes=payload.notes
    )
    await AuditService(session).record(
        AuditAction.ADMIN_PROVIDER_CHANGE,
        actor_user_id=user.id,
        target_type="provider_config",
        target_id=config.id,
        origin=origin,
        context={"provider": provider, "kind": kind, "enabled": payload.enabled},
    )
    return ProviderConfigResponse.of(config)


# --- job monitor (§99, P22-T03, P22-T04) -----------------------------------


class AdminJobResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    provider: str
    model: str | None
    retry_count: int
    error_code: str | None
    estimated_cost: float
    actual_cost: float | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, job: GenerationJob) -> AdminJobResponse:
        return cls(
            id=job.id,
            workspace_id=job.workspace_id,
            job_type=job.job_type,
            status=job.status,
            provider=job.provider,
            model=job.model,
            retry_count=job.retry_count,
            # The code, never `error_message`: a provider's verbatim complaint
            # can quote a customer's own prompt (§62), and an admin screen is
            # still not the place for it.
            error_code=job.error_code,
            estimated_cost=job.estimated_cost,
            actual_cost=job.actual_cost,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )


@router.get(
    "/jobs",
    response_model=list[AdminJobResponse],
    summary="Jobs across every workspace",
    dependencies=[_admin_only()],
)
async def list_jobs(
    session: SessionDep,
    status: JobStatus | None = None,
    job_type: JobType | None = None,
    provider: str | None = None,
    limit: int = 100,
) -> list[AdminJobResponse]:
    """§99's job monitor (P22-T03).

    Cross-tenant, which is why this router is guarded by platform admin rather
    than by a workspace role.
    """
    query = select(GenerationJob).order_by(GenerationJob.created_at.desc())
    if status is not None:
        query = query.where(GenerationJob.status == status)
    if job_type is not None:
        query = query.where(GenerationJob.job_type == job_type)
    if provider is not None:
        query = query.where(GenerationJob.provider == provider)

    result = await session.execute(query.limit(min(max(limit, 1), 500)))
    return [AdminJobResponse.of(job) for job in result.scalars().all()]


@router.get(
    "/jobs/failed",
    response_model=list[AdminJobResponse],
    summary="Recent failures",
    dependencies=[_admin_only()],
)
async def list_failed_jobs(
    session: SessionDep, hours: int = 24, limit: int = 100
) -> list[AdminJobResponse]:
    """§99's failure view (P22-T04).

    Separate from `/jobs?status=FAILED` because it also covers `TIMEOUT`, and
    because a time bound is the difference between a page that loads and one
    that scans a year of history.
    """
    since = datetime.now(UTC) - timedelta(hours=min(max(hours, 1), 720))
    result = await session.execute(
        select(GenerationJob)
        .where(
            GenerationJob.status.in_([JobStatus.FAILED, JobStatus.TIMEOUT]),
            GenerationJob.finished_at >= since,
        )
        .order_by(GenerationJob.finished_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    return [AdminJobResponse.of(job) for job in result.scalars().all()]


# --- analytics (§99, P22-T07, P22-T08) --------------------------------------


class ProviderUsage(BaseModel):
    provider: str
    total: int
    succeeded: int
    failed: int
    success_rate: float
    spend: float


class AnalyticsResponse(BaseModel):
    """§99's four numbers: volume, success rate, cost, failure rate."""

    window_hours: int
    total_jobs: int
    succeeded: int
    failed: int
    success_rate: float
    failure_rate: float
    total_spend: float
    workspaces_active: int
    by_provider: list[ProviderUsage]
    by_job_type: dict[str, int]


@router.get(
    "/analytics",
    response_model=AnalyticsResponse,
    summary="Generation volume, success rate and cost",
    dependencies=[_admin_only()],
)
async def analytics(session: SessionDep, hours: int = 24) -> AnalyticsResponse:
    """§99's acceptance: 生成量, 成功率, 成本, 失败率 (P22-T07, P22-T08).

    Computed with aggregates rather than by loading jobs and counting in
    Python. The difference does not matter at a thousand jobs and decides
    whether this page loads at a million.
    """
    window = min(max(hours, 1), 720)
    since = datetime.now(UTC) - timedelta(hours=window)

    totals = (
        await session.execute(
            select(
                func.count(GenerationJob.id),
                func.count(GenerationJob.id).filter(GenerationJob.status == JobStatus.COMPLETED),
                func.count(GenerationJob.id).filter(
                    GenerationJob.status.in_([JobStatus.FAILED, JobStatus.TIMEOUT])
                ),
                func.coalesce(func.sum(GenerationJob.actual_cost), 0.0),
                func.count(func.distinct(GenerationJob.workspace_id)),
            ).where(GenerationJob.created_at >= since)
        )
    ).one()

    total, succeeded, failed, spend, workspaces = totals
    total = int(total or 0)

    per_provider = (
        await session.execute(
            select(
                GenerationJob.provider,
                func.count(GenerationJob.id),
                func.count(GenerationJob.id).filter(GenerationJob.status == JobStatus.COMPLETED),
                func.count(GenerationJob.id).filter(
                    GenerationJob.status.in_([JobStatus.FAILED, JobStatus.TIMEOUT])
                ),
                func.coalesce(func.sum(GenerationJob.actual_cost), 0.0),
            )
            .where(GenerationJob.created_at >= since)
            .group_by(GenerationJob.provider)
        )
    ).all()

    per_type = (
        await session.execute(
            select(GenerationJob.job_type, func.count(GenerationJob.id))
            .where(GenerationJob.created_at >= since)
            .group_by(GenerationJob.job_type)
        )
    ).all()

    return AnalyticsResponse(
        window_hours=window,
        total_jobs=total,
        succeeded=int(succeeded or 0),
        failed=int(failed or 0),
        # Guarded against an empty window rather than reported as zero: no jobs
        # means no rate, and 0% success would read as a total outage.
        success_rate=round(int(succeeded or 0) / total, 4) if total else 0.0,
        failure_rate=round(int(failed or 0) / total, 4) if total else 0.0,
        total_spend=round(float(spend or 0.0), 2),
        workspaces_active=int(workspaces or 0),
        by_provider=[
            ProviderUsage(
                provider=name,
                total=int(count),
                succeeded=int(ok),
                failed=int(bad),
                success_rate=round(int(ok) / int(count), 4) if count else 0.0,
                spend=round(float(cost or 0.0), 2),
            )
            for name, count, ok, bad, cost in per_provider
        ],
        by_job_type={job_type.value: int(count) for job_type, count in per_type},
    )


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan_code: str
    status: str
    created_at: datetime
    job_count: int
    spend: float


@router.get(
    "/workspaces",
    response_model=list[WorkspaceSummary],
    summary="Workspaces and what they have spent",
    dependencies=[_admin_only()],
)
async def list_workspaces(session: SessionDep, limit: int = 100) -> list[WorkspaceSummary]:
    """§99's user list (P22-T02).

    Workspaces rather than users, because usage and cost are billed to a
    workspace and a user in three of them is three different customers.
    """
    count_rows = (
        await session.execute(
            select(GenerationJob.workspace_id, func.count(GenerationJob.id)).group_by(
                GenerationJob.workspace_id
            )
        )
    ).all()
    counts: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in count_rows}

    spend_rows = (
        await session.execute(
            select(
                CreditTransaction.workspace_id,
                func.coalesce(func.sum(CreditTransaction.amount), 0.0),
            )
            .where(CreditTransaction.transaction_type == CreditTransactionType.CAPTURE)
            .group_by(CreditTransaction.workspace_id)
        )
    ).all()
    spend: dict[uuid.UUID, float] = {row[0]: float(row[1] or 0.0) for row in spend_rows}

    result = await session.execute(
        select(Workspace).order_by(Workspace.created_at.desc()).limit(min(max(limit, 1), 500))
    )
    return [
        WorkspaceSummary(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            plan_code=workspace.plan_code.value,
            status=workspace.status.value,
            created_at=workspace.created_at,
            job_count=int(counts.get(workspace.id, 0)),
            spend=round(float(spend.get(workspace.id, 0.0)), 2),
        )
        for workspace in result.scalars().all()
    ]


__all__: list[str] = ["router"]
