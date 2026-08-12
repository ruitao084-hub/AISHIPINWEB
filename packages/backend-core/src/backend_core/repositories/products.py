"""Product, asset, fact and claim data access (§10.5-§10.8).

Every method is workspace-scoped by construction, and every *nested* lookup is
scoped by its parent as well: a fact is fetched by `(workspace_id, fact_id)`
and then checked against its product, never by id alone. Ids arriving from a
request are attacker-controlled, and one unscoped lookup is a cross-tenant read
(§61).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend_core.domain.enums import (
    ClaimStatus,
    ClaimType,
    FactSourceType,
    FactType,
    ProductAssetRole,
    ProductStatus,
    VerificationStatus,
)
from backend_core.domain.models import (
    MediaAsset,
    Product,
    ProductAsset,
    ProductClaim,
    ProductFact,
)


class ProductRepository:
    """Reads and writes for products and everything hanging off them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- products ----------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        category: str,
        brand_name: str | None = None,
        sku: str | None = None,
        description: str | None = None,
    ) -> Product:
        product = Product(
            workspace_id=workspace_id,
            name=name.strip(),
            category=category.strip(),
            brand_name=brand_name.strip() if brand_name else None,
            sku=sku.strip() if sku else None,
            description=description,
            status=ProductStatus.DRAFT,
        )
        self._session.add(product)
        await self._session.flush()
        return product

    async def get(self, workspace_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(
                Product.id == product_id,
                Product.workspace_id == workspace_id,
                Product.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        status: ProductStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Product]:
        statement = (
            select(Product)
            .where(Product.workspace_id == workspace_id, Product.deleted_at.is_(None))
            .order_by(Product.created_at.desc(), Product.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(Product.status == status)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    # -- assets ------------------------------------------------------------

    async def attach_asset(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        media_asset_id: uuid.UUID,
        asset_role: ProductAssetRole,
        is_primary: bool,
        sort_order: int,
    ) -> ProductAsset:
        link = ProductAsset(
            workspace_id=workspace_id,
            product_id=product_id,
            media_asset_id=media_asset_id,
            asset_role=asset_role,
            is_primary=is_primary,
            sort_order=sort_order,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def get_asset_link(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, link_id: uuid.UUID
    ) -> ProductAsset | None:
        result = await self._session.execute(
            select(ProductAsset).where(
                ProductAsset.id == link_id,
                ProductAsset.product_id == product_id,
                ProductAsset.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_assets(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> list[ProductAsset]:
        """A product's images, primary first, then by explicit order.

        `selectinload` on the media asset because the caller always needs the
        file's dimensions and key to render a thumbnail — leaving it lazy would
        raise (models use ``lazy="raise"``) or, worse, issue N queries.
        """
        result = await self._session.execute(
            select(ProductAsset)
            .options(selectinload(ProductAsset.media_asset))
            .where(
                ProductAsset.product_id == product_id,
                ProductAsset.workspace_id == workspace_id,
            )
            .order_by(
                ProductAsset.is_primary.desc(),
                ProductAsset.sort_order.asc(),
                ProductAsset.created_at.asc(),
            )
        )
        return list(result.scalars().all())

    async def count_assets(self, workspace_id: uuid.UUID, product_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ProductAsset)
            .where(
                ProductAsset.product_id == product_id,
                ProductAsset.workspace_id == workspace_id,
            )
        )
        return int(result.scalar_one())

    async def clear_primary(self, workspace_id: uuid.UUID, product_id: uuid.UUID) -> None:
        """Unset whichever asset is currently primary.

        Run before setting a new one: the partial unique index permits exactly
        one primary per product, so the old flag must be dropped in the same
        transaction or the insert collides.
        """
        result = await self._session.execute(
            select(ProductAsset).where(
                ProductAsset.product_id == product_id,
                ProductAsset.workspace_id == workspace_id,
                ProductAsset.is_primary.is_(True),
            )
        )
        for link in result.scalars().all():
            link.is_primary = False
        await self._session.flush()

    async def next_sort_order(self, workspace_id: uuid.UUID, product_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(ProductAsset.sort_order), -1)).where(
                ProductAsset.product_id == product_id,
                ProductAsset.workspace_id == workspace_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def get_media_asset(
        self, workspace_id: uuid.UUID, media_asset_id: uuid.UUID
    ) -> MediaAsset | None:
        result = await self._session.execute(
            select(MediaAsset).where(
                MediaAsset.id == media_asset_id,
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    # -- facts -------------------------------------------------------------

    async def create_fact(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        fact_type: FactType,
        key: str,
        value_text: str,
        value_json: dict[str, Any] | None,
        source_type: FactSourceType,
        source_asset_id: uuid.UUID | None,
        verification_status: VerificationStatus,
        verified_by_user_id: uuid.UUID | None = None,
        verified_at: datetime | None = None,
    ) -> ProductFact:
        """Insert a fact.

        The verification stamp is part of the *insert*, not a follow-up write:
        `ck_product_facts_verified_facts_have_a_timestamp` rejects a VERIFIED
        row with no `verified_at`, so stamping after the flush fails outright.
        That constraint caught this exact mistake the first time this code ran.
        """
        fact = ProductFact(
            workspace_id=workspace_id,
            product_id=product_id,
            fact_type=fact_type,
            key=key.strip(),
            value_text=value_text.strip(),
            value_json=value_json,
            source_type=source_type,
            source_asset_id=source_asset_id,
            verification_status=verification_status,
            verified_by_user_id=verified_by_user_id,
            verified_at=verified_at,
        )
        self._session.add(fact)
        await self._session.flush()
        return fact

    async def get_fact(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID
    ) -> ProductFact | None:
        result = await self._session.execute(
            select(ProductFact).where(
                ProductFact.id == fact_id,
                ProductFact.product_id == product_id,
                ProductFact.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_facts(
        self,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        *,
        verification_status: VerificationStatus | None = None,
    ) -> list[ProductFact]:
        statement = (
            select(ProductFact)
            .where(
                ProductFact.product_id == product_id,
                ProductFact.workspace_id == workspace_id,
            )
            .order_by(ProductFact.fact_type.asc(), ProductFact.created_at.asc())
        )
        if verification_status is not None:
            statement = statement.where(ProductFact.verification_status == verification_status)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_facts_by_ids(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_ids: list[uuid.UUID]
    ) -> list[ProductFact]:
        """Resolve a claim's cited facts, scoped to the claim's own product.

        The scoping is the point: without it, a claim could cite a verified
        fact belonging to a different product — or a different tenant — and
        appear substantiated.
        """
        if not fact_ids:
            return []
        result = await self._session.execute(
            select(ProductFact).where(
                ProductFact.id.in_(fact_ids),
                ProductFact.product_id == product_id,
                ProductFact.workspace_id == workspace_id,
            )
        )
        return list(result.scalars().all())

    # -- claims ------------------------------------------------------------

    async def create_claim(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        claim_text: str,
        claim_type: ClaimType,
        source_fact_ids: list[uuid.UUID],
        risk_level: Any,
    ) -> ProductClaim:
        claim = ProductClaim(
            workspace_id=workspace_id,
            product_id=product_id,
            claim_text=claim_text.strip(),
            claim_type=claim_type,
            source_fact_ids=[str(fact_id) for fact_id in source_fact_ids],
            risk_level=risk_level,
            status=ClaimStatus.SUGGESTED,
        )
        self._session.add(claim)
        await self._session.flush()
        return claim

    async def get_claim(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, claim_id: uuid.UUID
    ) -> ProductClaim | None:
        result = await self._session.execute(
            select(ProductClaim).where(
                ProductClaim.id == claim_id,
                ProductClaim.product_id == product_id,
                ProductClaim.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_claims(
        self,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        *,
        status: ClaimStatus | None = None,
    ) -> list[ProductClaim]:
        statement = (
            select(ProductClaim)
            .where(
                ProductClaim.product_id == product_id,
                ProductClaim.workspace_id == workspace_id,
            )
            .order_by(ProductClaim.created_at.asc())
        )
        if status is not None:
            statement = statement.where(ProductClaim.status == status)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_verified_claims_citing(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID
    ) -> list[ProductClaim]:
        """Every `VERIFIED` claim whose evidence includes ``fact_id``.

        Used when a fact is rejected or edited: the claims it substantiated are
        no longer substantiated, and leaving them `VERIFIED` would let a script
        quote evidence that has since been withdrawn (§13).

        ``@>`` is JSONB containment, which the index-free array scan handles
        fine at the scale of one product's claims.
        """
        result = await self._session.execute(
            select(ProductClaim).where(
                ProductClaim.product_id == product_id,
                ProductClaim.workspace_id == workspace_id,
                ProductClaim.status == ClaimStatus.VERIFIED,
                ProductClaim.source_fact_ids.op("@>")(func.jsonb_build_array(str(fact_id))),
            )
        )
        return list(result.scalars().all())
