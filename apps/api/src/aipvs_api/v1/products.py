"""Product and Truth Layer endpoints (taskbook §10.5-§10.8, §13, §109).

The verification endpoints are the ones that matter. Everything else here is
ordinary CRUD; `POST .../facts/{id}/verify` and `POST .../claims/{id}/verify`
are where a human takes responsibility for something the platform will later
say out loud, so they are separate, explicit actions rather than a `status`
field a client could PATCH.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import (
    CurrentUser,
    OriginDep,
    SessionDep,
    rate_limited,
    require_permission,
)
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    AnalysisStatus,
    AuditAction,
    ClaimRiskLevel,
    ClaimStatus,
    ClaimType,
    FactSourceType,
    FactType,
    Permission,
    ProductAssetRole,
    ProductStatus,
    VerificationStatus,
)
from backend_core.domain.models import (
    Product,
    ProductAnalysis,
    ProductAsset,
    ProductClaim,
    ProductFact,
)
from backend_core.services.audit import AuditService
from backend_core.services.product_analysis import ProductAnalysisService
from backend_core.services.product_truth import ProductTruthService
from backend_core.services.products import ProductService

router = APIRouter(prefix="/workspaces/{workspace_id}/products", tags=["products"])


# --- responses -------------------------------------------------------------


class ProductResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    brand_name: str | None
    sku: str | None
    description: str | None
    status: ProductStatus
    ai_summary: str | None
    visual_dna: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, product: Product) -> ProductResponse:
        return cls(
            id=product.id,
            name=product.name,
            category=product.category,
            brand_name=product.brand_name,
            sku=product.sku,
            description=product.description,
            status=product.status,
            ai_summary=product.ai_summary,
            visual_dna=product.visual_dna,
            created_at=product.created_at,
        )


class ProductAssetResponse(BaseModel):
    id: uuid.UUID
    media_asset_id: uuid.UUID
    asset_role: ProductAssetRole
    is_primary: bool
    sort_order: int
    width: int | None
    height: int | None
    original_filename: str | None

    @classmethod
    def of(cls, link: ProductAsset) -> ProductAssetResponse:
        media = link.media_asset
        return cls(
            id=link.id,
            media_asset_id=link.media_asset_id,
            asset_role=link.asset_role,
            is_primary=link.is_primary,
            sort_order=link.sort_order,
            width=media.width,
            height=media.height,
            original_filename=media.original_filename,
        )


class ProductFactResponse(BaseModel):
    id: uuid.UUID
    fact_type: FactType
    key: str
    value_text: str
    value_json: dict[str, Any] | None
    source_type: FactSourceType
    source_asset_id: uuid.UUID | None
    verification_status: VerificationStatus = Field(
        description=(
            "Only VERIFIED facts may back a claim or reach a generated script (§13). "
            "Anything the AI produced is AI_INFERRED until a person confirms it."
        )
    )
    verified_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, fact: ProductFact) -> ProductFactResponse:
        return cls(
            id=fact.id,
            fact_type=fact.fact_type,
            key=fact.key,
            value_text=fact.value_text,
            value_json=fact.value_json,
            source_type=fact.source_type,
            source_asset_id=fact.source_asset_id,
            verification_status=fact.verification_status,
            verified_at=fact.verified_at,
            created_at=fact.created_at,
        )


class ProductClaimResponse(BaseModel):
    id: uuid.UUID
    claim_text: str
    claim_type: ClaimType
    source_fact_ids: list[uuid.UUID]
    status: ClaimStatus = Field(
        description="Only VERIFIED claims may be used in a generated script (§109)."
    )
    risk_level: ClaimRiskLevel
    verified_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, claim: ProductClaim) -> ProductClaimResponse:
        return cls(
            id=claim.id,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            source_fact_ids=[uuid.UUID(value) for value in claim.source_fact_ids],
            status=claim.status,
            risk_level=claim.risk_level,
            verified_at=claim.verified_at,
            created_at=claim.created_at,
        )


# --- requests --------------------------------------------------------------


class CreateProductRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=120)
    brand_name: str | None = Field(default=None, max_length=120)
    sku: str | None = Field(default=None, max_length=120)
    description: str | None = None


class UpdateProductRequest(ApiRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=120)
    brand_name: str | None = Field(default=None, max_length=120)
    sku: str | None = Field(default=None, max_length=120)
    description: str | None = None


class AttachAssetRequest(ApiRequest):
    media_asset_id: uuid.UUID
    asset_role: ProductAssetRole = ProductAssetRole.OTHER
    is_primary: bool = False


class CreateFactRequest(ApiRequest):
    fact_type: FactType
    key: str = Field(min_length=1, max_length=120)
    value_text: str = Field(min_length=1)
    value_json: dict[str, Any] | None = None
    source_asset_id: uuid.UUID | None = None
    verify: bool = Field(
        default=False,
        description=(
            "Confirm the fact as you create it. Only honoured for facts a user "
            "supplied — AI-sourced facts are always AI_INFERRED (§13)."
        ),
    )


class UpdateFactRequest(ApiRequest):
    fact_type: FactType | None = None
    key: str | None = Field(default=None, min_length=1, max_length=120)
    value_text: str | None = Field(default=None, min_length=1)
    value_json: dict[str, Any] | None = None
    verify: bool = Field(
        default=False,
        description=(
            "Re-confirm after editing. Without it, changing a value drops the "
            "fact back to USER_PROVIDED, because whatever was verified before "
            "was a different statement."
        ),
    )


class CreateClaimRequest(ApiRequest):
    claim_text: str = Field(min_length=1)
    claim_type: ClaimType
    source_fact_ids: list[uuid.UUID] = Field(default_factory=list)
    risk_level: ClaimRiskLevel | None = None


# --- product routes --------------------------------------------------------


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def create_product(
    workspace_id: uuid.UUID,
    payload: CreateProductRequest,
    session: SessionDep,
    origin: OriginDep,
) -> ProductResponse:
    """Create a product. Always starts in DRAFT (§104)."""
    product = await ProductService(session).create(
        workspace_id=workspace_id,
        name=payload.name,
        category=payload.category,
        brand_name=payload.brand_name,
        sku=payload.sku,
        description=payload.description,
    )
    await AuditService(session).record(
        AuditAction.PRODUCT_CREATE,
        workspace_id=workspace_id,
        target_type="product",
        target_id=product.id,
        origin=origin,
        context={"name": product.name},
    )
    return ProductResponse.of(product)


@router.get(
    "",
    response_model=list[ProductResponse],
    summary="List products",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_products(
    workspace_id: uuid.UUID,
    session: SessionDep,
    product_status: Annotated[ProductStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductResponse]:
    products = await ProductService(session).list_products(
        workspace_id=workspace_id, status=product_status, limit=limit, offset=offset
    )
    return [ProductResponse.of(product) for product in products]


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get a product",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def get_product(
    workspace_id: uuid.UUID, product_id: uuid.UUID, session: SessionDep
) -> ProductResponse:
    product = await ProductService(session).get(workspace_id=workspace_id, product_id=product_id)
    return ProductResponse.of(product)


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Edit a product",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def update_product(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: UpdateProductRequest,
    session: SessionDep,
) -> ProductResponse:
    """Edit descriptive fields.

    `status` is deliberately not editable here — §105 forbids arbitrary status
    writes, so transitions go through their own endpoints.
    """
    product = await ProductService(session).update(
        workspace_id=workspace_id,
        product_id=product_id,
        name=payload.name,
        category=payload.category,
        brand_name=payload.brand_name,
        sku=payload.sku,
        description=payload.description,
    )
    return ProductResponse.of(product)


@router.post(
    "/{product_id}/archive",
    response_model=ProductResponse,
    summary="Archive a product",
    dependencies=[require_permission(Permission.PRODUCT_DELETE)],
)
async def archive_product(
    workspace_id: uuid.UUID, product_id: uuid.UUID, session: SessionDep, origin: OriginDep
) -> ProductResponse:
    product = await ProductService(session).archive(
        workspace_id=workspace_id, product_id=product_id
    )
    await AuditService(session).record(
        AuditAction.PRODUCT_DELETE,
        workspace_id=workspace_id,
        target_type="product",
        target_id=product.id,
        origin=origin,
        context={"name": product.name, "soft_delete": True},
    )
    return ProductResponse.of(product)


@router.post(
    "/{product_id}/ready",
    response_model=ProductResponse,
    summary="Mark a reviewed product ready",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def mark_product_ready(
    workspace_id: uuid.UUID, product_id: uuid.UUID, session: SessionDep
) -> ProductResponse:
    """Move a product to READY.

    Refused unless at least one fact has been verified: a product with nothing
    confirmed has nothing a script could truthfully say (§13).
    """
    await ProductTruthService(session).mark_reviewed(
        workspace_id=workspace_id, product_id=product_id
    )
    product = await ProductService(session).get(workspace_id=workspace_id, product_id=product_id)
    return ProductResponse.of(product)


# --- imagery ---------------------------------------------------------------


@router.post(
    "/{product_id}/assets",
    response_model=ProductAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach an image to a product",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def attach_asset(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: AttachAssetRequest,
    session: SessionDep,
) -> ProductAssetResponse:
    """Attach an already-uploaded media asset (§10.6).

    The first image attached becomes the primary one automatically.
    """
    service = ProductService(session)
    await service.attach_asset(
        workspace_id=workspace_id,
        product_id=product_id,
        media_asset_id=payload.media_asset_id,
        asset_role=payload.asset_role,
        is_primary=payload.is_primary,
    )
    # Re-read through the listing query so the media asset is eagerly loaded;
    # the models use lazy="raise", so touching it off the freshly inserted row
    # would raise rather than silently issue a query.
    links = await service.list_assets(workspace_id=workspace_id, product_id=product_id)
    attached = next(link for link in links if link.media_asset_id == payload.media_asset_id)
    return ProductAssetResponse.of(attached)


@router.get(
    "/{product_id}/assets",
    response_model=list[ProductAssetResponse],
    summary="List a product's images",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_product_assets(
    workspace_id: uuid.UUID, product_id: uuid.UUID, session: SessionDep
) -> list[ProductAssetResponse]:
    links = await ProductService(session).list_assets(
        workspace_id=workspace_id, product_id=product_id
    )
    return [ProductAssetResponse.of(link) for link in links]


@router.post(
    "/{product_id}/assets/{link_id}/primary",
    response_model=ProductAssetResponse,
    summary="Set the primary image",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def set_primary_asset(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    link_id: uuid.UUID,
    session: SessionDep,
) -> ProductAssetResponse:
    service = ProductService(session)
    await service.set_primary_asset(
        workspace_id=workspace_id, product_id=product_id, link_id=link_id
    )
    links = await service.list_assets(workspace_id=workspace_id, product_id=product_id)
    primary = next(link for link in links if link.id == link_id)
    return ProductAssetResponse.of(primary)


@router.delete(
    "/{product_id}/assets/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Detach an image",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def detach_asset(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    link_id: uuid.UUID,
    session: SessionDep,
) -> None:
    """Detach an image. The underlying file is untouched (§163 reclaims it)."""
    await ProductService(session).detach_asset(
        workspace_id=workspace_id, product_id=product_id, link_id=link_id
    )


# --- facts (P5-T04, P5-T06) ------------------------------------------------


@router.post(
    "/{product_id}/facts",
    response_model=ProductFactResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a product fact",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def create_fact(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: CreateFactRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ProductFactResponse:
    """Record something true about the product (§10.7).

    Facts created here are `USER_INPUT`: this endpoint is a person typing.
    The analyser's AI-sourced facts go through the same service and are forced
    to `AI_INFERRED` (§13) — there is no route that can create a verified AI
    fact.
    """
    fact = await ProductTruthService(session).create_fact(
        workspace_id=workspace_id,
        product_id=product_id,
        fact_type=payload.fact_type,
        key=payload.key,
        value_text=payload.value_text,
        value_json=payload.value_json,
        source_type=FactSourceType.USER_INPUT,
        source_asset_id=payload.source_asset_id,
        verified_by=user if payload.verify else None,
    )
    return ProductFactResponse.of(fact)


@router.get(
    "/{product_id}/facts",
    response_model=list[ProductFactResponse],
    summary="List product facts",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_facts(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    session: SessionDep,
    verification_status: Annotated[VerificationStatus | None, Query()] = None,
) -> list[ProductFactResponse]:
    facts = await ProductTruthService(session).list_facts(
        workspace_id=workspace_id, product_id=product_id, verification_status=verification_status
    )
    return [ProductFactResponse.of(fact) for fact in facts]


@router.patch(
    "/{product_id}/facts/{fact_id}",
    response_model=ProductFactResponse,
    summary="Edit a fact",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def update_fact(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: UpdateFactRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ProductFactResponse:
    """Edit a fact, optionally re-confirming it.

    Changing the value withdraws any prior verification and sends claims that
    cited it back for review — they were approved against a different
    statement.
    """
    fact = await ProductTruthService(session).update_fact(
        workspace_id=workspace_id,
        product_id=product_id,
        fact_id=fact_id,
        user=user,
        fact_type=payload.fact_type,
        key=payload.key,
        value_text=payload.value_text,
        value_json=payload.value_json,
        verify=payload.verify,
    )
    return ProductFactResponse.of(fact)


@router.post(
    "/{product_id}/facts/{fact_id}/verify",
    response_model=ProductFactResponse,
    summary="Confirm a fact",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def verify_fact(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    fact_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    origin: OriginDep,
) -> ProductFactResponse:
    """Take responsibility for a fact (§13).

    Records who confirmed it and when. This is the only way a fact becomes
    usable as evidence.
    """
    fact = await ProductTruthService(session).verify_fact(
        workspace_id=workspace_id, product_id=product_id, fact_id=fact_id, user=user
    )
    await AuditService(session).record(
        AuditAction.FACT_VERIFY,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        target_type="product_fact",
        target_id=fact.id,
        origin=origin,
        context={"product_id": str(product_id), "outcome": fact.verification_status.value},
    )
    return ProductFactResponse.of(fact)


@router.post(
    "/{product_id}/facts/{fact_id}/reject",
    response_model=ProductFactResponse,
    summary="Reject a fact",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def reject_fact(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    fact_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
) -> ProductFactResponse:
    """Mark a fact wrong.

    Any verified claim that cited it is demoted to SUGGESTED in the same
    transaction — evidence withdrawn means the sentence built on it is no
    longer approved.
    """
    fact = await ProductTruthService(session).reject_fact(
        workspace_id=workspace_id, product_id=product_id, fact_id=fact_id, user=user
    )
    return ProductFactResponse.of(fact)


# --- claims (P5-T05, P5-T07) -----------------------------------------------


@router.post(
    "/{product_id}/claims",
    response_model=ProductClaimResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a claim",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def create_claim(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: CreateClaimRequest,
    session: SessionDep,
) -> ProductClaimResponse:
    """Propose a marketing claim. Always created SUGGESTED (§10.8)."""
    claim = await ProductTruthService(session).create_claim(
        workspace_id=workspace_id,
        product_id=product_id,
        claim_text=payload.claim_text,
        claim_type=payload.claim_type,
        source_fact_ids=payload.source_fact_ids,
        risk_level=payload.risk_level,
    )
    return ProductClaimResponse.of(claim)


@router.get(
    "/{product_id}/claims",
    response_model=list[ProductClaimResponse],
    summary="List claims",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_claims(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    session: SessionDep,
    claim_status: Annotated[ClaimStatus | None, Query(alias="status")] = None,
) -> list[ProductClaimResponse]:
    claims = await ProductTruthService(session).list_claims(
        workspace_id=workspace_id, product_id=product_id, status=claim_status
    )
    return [ProductClaimResponse.of(claim) for claim in claims]


@router.get(
    "/{product_id}/claims/verified",
    response_model=list[ProductClaimResponse],
    summary="Claims a script may use",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_verified_claims(
    workspace_id: uuid.UUID, product_id: uuid.UUID, session: SessionDep
) -> list[ProductClaimResponse]:
    """§109's `get_verified_claims`, exposed over HTTP.

    A separate route rather than a query parameter, so "the claims that may be
    broadcast" is a distinct thing a caller asks for rather than a filter it
    has to remember to apply.
    """
    claims = await ProductTruthService(session).get_verified_claims(
        workspace_id=workspace_id, product_id=product_id
    )
    return [ProductClaimResponse.of(claim) for claim in claims]


@router.post(
    "/{product_id}/claims/{claim_id}/verify",
    response_model=ProductClaimResponse,
    summary="Approve a claim for use",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def verify_claim(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    claim_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    origin: OriginDep,
) -> ProductClaimResponse:
    """Approve a claim (§13, §109).

    Refused with `CLAIM_NOT_VERIFIED` when the claim asserts something
    checkable and cites no verified fact. Only EMOTIONAL claims — which assert
    nothing substantiable — are exempt.
    """
    claim = await ProductTruthService(session).verify_claim(
        workspace_id=workspace_id, product_id=product_id, claim_id=claim_id, user=user
    )
    await AuditService(session).record(
        AuditAction.CLAIM_VERIFY,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        target_type="product_claim",
        target_id=claim.id,
        origin=origin,
        context={"product_id": str(product_id), "outcome": claim.status.value},
    )
    return ProductClaimResponse.of(claim)


@router.post(
    "/{product_id}/claims/{claim_id}/reject",
    response_model=ProductClaimResponse,
    summary="Reject a claim",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def reject_claim(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    claim_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    origin: OriginDep,
) -> ProductClaimResponse:
    claim = await ProductTruthService(session).reject_claim(
        workspace_id=workspace_id, product_id=product_id, claim_id=claim_id, user=user
    )
    await AuditService(session).record(
        AuditAction.CLAIM_VERIFY,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        target_type="product_claim",
        target_id=claim.id,
        origin=origin,
        context={"product_id": str(product_id), "outcome": claim.status.value},
    )
    return ProductClaimResponse.of(claim)


# --- analysis (P6-T06, P6-T07) ---------------------------------------------


class ProductAnalysisResponse(BaseModel):
    id: uuid.UUID
    status: AnalysisStatus
    provider: str
    prompt_key: str = Field(description="Which registered prompt produced this result (§15).")
    prompt_version: int
    model: str | None
    analyzed_asset_ids: list[uuid.UUID]
    created_fact_count: int
    created_claim_count: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    error_code: str | None
    created_at: datetime

    @classmethod
    def of(cls, analysis: ProductAnalysis) -> ProductAnalysisResponse:
        return cls(
            id=analysis.id,
            status=analysis.status,
            provider=analysis.provider,
            prompt_key=analysis.prompt_key,
            prompt_version=analysis.prompt_version,
            model=analysis.model,
            analyzed_asset_ids=[uuid.UUID(value) for value in analysis.analyzed_asset_ids],
            created_fact_count=analysis.created_fact_count,
            created_claim_count=analysis.created_claim_count,
            input_tokens=analysis.input_tokens,
            output_tokens=analysis.output_tokens,
            latency_ms=analysis.latency_ms,
            # `error_message` is deliberately absent: it can carry a provider's
            # verbatim complaint about the customer's own imagery, and §62 keeps
            # that out of a response a whole workspace can read. The code is
            # enough to act on; the message stays in the row for support.
            error_code=analysis.error_code,
            created_at=analysis.created_at,
        )


@router.post(
    "/{product_id}/analyze",
    response_model=ProductAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Analyse the product's images",
    # GENERATION_RUN, not PRODUCT_WRITE: a vision call costs money, and §40
    # deliberately does not let write access imply spending it.
    dependencies=[require_permission(Permission.GENERATION_RUN), rate_limited("analyze")],
)
async def analyze_product(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    session: SessionDep,
) -> ProductAnalysisResponse:
    """Run vision analysis and file the results for review (§14).

    Everything the model observes lands as an `AI_INFERRED` fact and everything
    it suggests lands as a `SUGGESTED` claim — nothing here is publishable, and
    the product moves to `REVIEW_REQUIRED` rather than to a ready state.

    Synchronous for now, which §83 permits at this length. PHASE 9 moves it
    behind the job queue, at which point this returns the analysis in `PENDING`
    and the client polls.
    """
    analysis = await ProductAnalysisService(session).analyze(
        workspace_id=workspace_id, product_id=product_id
    )
    return ProductAnalysisResponse.of(analysis)


@router.get(
    "/{product_id}/analyses",
    response_model=list[ProductAnalysisResponse],
    summary="Analysis history",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_analyses(
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ProductAnalysisResponse]:
    """Past runs, newest first, failures included.

    A refused or timed-out attempt is exactly what a reviewer needs to see;
    hiding it would make the product look as though nothing had been tried.
    """
    analyses = await ProductAnalysisService(session).history(
        workspace_id=workspace_id, product_id=product_id, limit=limit
    )
    return [ProductAnalysisResponse.of(analysis) for analysis in analyses]
