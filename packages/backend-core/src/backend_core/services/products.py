"""Product lifecycle and imagery (taskbook §10.5, §10.6, §104, P5-T02/T03).

Status is never assigned directly. §105 forbids arbitrary status writes, so
every change goes through :meth:`ProductService.transition`, which refuses
anything the §104 machine does not allow. The two transitions that happen
automatically — gaining and losing imagery — are still routed through it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    AssetType,
    ProductAssetRole,
    ProductStatus,
    UploadStatus,
    can_transition_product,
)
from backend_core.domain.models import Product, ProductAsset
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.repositories.products import ProductRepository

logger = get_logger(__name__)


class InvalidProductTransitionError(AppError):
    """The requested status change is not one §104 permits."""

    code = ErrorCode.PROJECT_INVALID_STATE
    http_status = 409
    default_message = "That status change is not allowed for this product."


class DuplicateSkuError(AppError):
    """Another product in this workspace already uses that SKU."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 409
    default_message = "Another product in this workspace already uses that SKU."


class ProductService:
    """Create products and manage the imagery attached to them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._products = ProductRepository(session)

    # -- lifecycle ---------------------------------------------------------

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
        """Create a product in `DRAFT`.

        Always `DRAFT`, never a status the caller chose: a product with no
        imagery and no verified facts is not ready for anything, and letting a
        client assert otherwise would put the Truth Layer's entry condition in
        the client's hands.
        """
        try:
            product = await self._products.create(
                workspace_id=workspace_id,
                name=name,
                category=category,
                brand_name=brand_name,
                sku=sku,
                description=description,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            if _violated(exc, "uq_products_workspace_id_sku"):
                raise DuplicateSkuError() from exc
            raise

        logger.info("product_created", extra={"product_id": str(product.id)})
        return product

    async def get(self, *, workspace_id: uuid.UUID, product_id: uuid.UUID) -> Product:
        product = await self._products.get(workspace_id, product_id)
        if product is None:
            raise NotFoundError("That product does not exist.")
        return product

    async def list_products(
        self,
        *,
        workspace_id: uuid.UUID,
        status: ProductStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Product]:
        return await self._products.list_for_workspace(
            workspace_id, status=status, limit=limit, offset=offset
        )

    async def update(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        name: str | None = None,
        category: str | None = None,
        brand_name: str | None = None,
        sku: str | None = None,
        description: str | None = None,
    ) -> Product:
        """Edit descriptive fields. Status is not among them, by design."""
        product = await self.get(workspace_id=workspace_id, product_id=product_id)

        if name is not None:
            product.name = name.strip()
        if category is not None:
            product.category = category.strip()
        if brand_name is not None:
            product.brand_name = brand_name.strip() or None
        if sku is not None:
            product.sku = sku.strip() or None
        if description is not None:
            product.description = description or None

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            if _violated(exc, "uq_products_workspace_id_sku"):
                raise DuplicateSkuError() from exc
            raise
        return product

    async def transition(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, target: ProductStatus
    ) -> Product:
        """Move a product to ``target``, or refuse (§104).

        The refusal is the feature. A product that reached `READY` without
        passing through review would be one whose facts nobody confirmed, and
        §13's guarantee rests on that path being the only one.
        """
        product = await self.get(workspace_id=workspace_id, product_id=product_id)
        return await self._transition(product, target)

    async def _transition(self, product: Product, target: ProductStatus) -> Product:
        if not can_transition_product(product.status, target):
            raise InvalidProductTransitionError(
                f"A product cannot go from {product.status.value} to {target.value}.",
                details={"from": product.status.value, "to": target.value},
            )
        if product.status is not target:
            logger.info(
                "product_status_changed",
                extra={
                    "product_id": str(product.id),
                    "from_status": product.status.value,
                    "to_status": target.value,
                },
            )
            product.status = target
            await self._session.flush()
        return product

    async def archive(self, *, workspace_id: uuid.UUID, product_id: uuid.UUID) -> Product:
        """Archive a product. Reachable from every non-archived state (§104)."""
        return await self.transition(
            workspace_id=workspace_id, product_id=product_id, target=ProductStatus.ARCHIVED
        )

    # -- imagery -----------------------------------------------------------

    async def attach_asset(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        media_asset_id: uuid.UUID,
        asset_role: ProductAssetRole = ProductAssetRole.OTHER,
        is_primary: bool = False,
    ) -> ProductAsset:
        """Attach an uploaded image to a product (§10.6).

        The asset must be `READY`: a `PENDING` upload has no confirmed bytes
        behind it (§12), and attaching one would let a product reference a file
        that never finished uploading — or failed validation.
        """
        product = await self.get(workspace_id=workspace_id, product_id=product_id)

        media_asset = await self._products.get_media_asset(workspace_id, media_asset_id)
        if media_asset is None:
            raise NotFoundError("That media asset does not exist.")
        if media_asset.upload_status is not UploadStatus.READY:
            raise ValidationError(
                "That upload has not finished. Complete it before attaching it.",
                details={"upload_status": media_asset.upload_status.value},
            )
        if media_asset.asset_type not in (AssetType.IMAGE, AssetType.VIDEO):
            raise ValidationError(
                "Only images and video can be attached to a product.",
                details={"asset_type": media_asset.asset_type.value},
            )

        # The first image becomes the primary one automatically. A product with
        # images but no hero shot has no defined thumbnail, and making the user
        # perform a second explicit step to get the obvious default is friction
        # for nothing.
        existing = await self._products.count_assets(workspace_id, product_id)
        primary = is_primary or existing == 0
        if primary:
            await self._products.clear_primary(workspace_id, product_id)

        sort_order = await self._products.next_sort_order(workspace_id, product_id)

        try:
            link = await self._products.attach_asset(
                workspace_id=workspace_id,
                product_id=product_id,
                media_asset_id=media_asset_id,
                asset_role=asset_role,
                is_primary=primary,
                sort_order=sort_order,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            if _violated(exc, "uq_product_asset"):
                raise ValidationError("That image is already attached to this product.") from exc
            raise

        if product.status is ProductStatus.DRAFT:
            await self._transition(product, ProductStatus.ASSETS_READY)

        return link

    async def set_primary_asset(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, link_id: uuid.UUID
    ) -> ProductAsset:
        """Promote one attached image to the product's hero shot."""
        link = await self._products.get_asset_link(workspace_id, product_id, link_id)
        if link is None:
            raise NotFoundError("That image is not attached to this product.")

        # Clearing first is not optional: the partial unique index allows one
        # primary per product, so setting a second without clearing the first
        # is a constraint violation rather than a silent overwrite.
        await self._products.clear_primary(workspace_id, product_id)
        link.is_primary = True
        await self._session.flush()
        return link

    async def detach_asset(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, link_id: uuid.UUID
    ) -> None:
        """Remove an image from a product.

        The underlying `MediaAsset` is untouched — it may be attached to other
        products, and reclaiming storage is the collector's job (§163).
        """
        product = await self.get(workspace_id=workspace_id, product_id=product_id)
        link = await self._products.get_asset_link(workspace_id, product_id, link_id)
        if link is None:
            raise NotFoundError("That image is not attached to this product.")

        was_primary = link.is_primary
        await self._session.delete(link)
        await self._session.flush()

        remaining = await self._products.list_assets(workspace_id, product_id)

        # Losing the primary must not leave the product without one; the next
        # image in order takes over.
        if was_primary and remaining:
            remaining[0].is_primary = True
            await self._session.flush()

        if not remaining and product.status is ProductStatus.ASSETS_READY:
            await self._transition(product, ProductStatus.DRAFT)

    async def list_assets(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> list[ProductAsset]:
        await self.get(workspace_id=workspace_id, product_id=product_id)
        return await self._products.list_assets(workspace_id, product_id)


def _violated(exc: IntegrityError, constraint: str) -> bool:
    """Whether ``exc`` was caused by a specific named constraint.

    Read from psycopg's diagnostics rather than by matching the message text.
    Mapping every `IntegrityError` to one friendly error is how PHASE 3 ended
    up reporting "email already registered" for a slug-length violation.
    """
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    return name == constraint
