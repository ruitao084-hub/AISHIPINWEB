"""Analyse a product's imagery and file the results (§14, P6-T06 / P6-T07).

This is the seam where a language model's output meets the Truth Layer, and the
mapping is the whole safety story.

**Observed fields become `AI_INFERRED` facts.** Colours, materials, legible
text, structural and visual features describe what is in the photograph, so
they are candidate facts — and they arrive unverified, because a model that
misreads a model number has produced a wrong product, not a small error.

**Inferred fields never become facts.** `possible_use_cases` and
`possible_selling_points` are speculation, and §109 forbids using
`possible_selling_points` as factual advertising. They become `SUGGESTED`
claims, which cannot be approved without a verified fact behind them (§13) —
so the model's guesses enter the system marked as guesses and stay that way
until a person supplies evidence.

The enforcement is not a convention here: :func:`_fact_specs` iterates
:data:`OBSERVED_FIELDS` only, and the inferred fields are structurally unable
to reach `create_fact`.
"""

from __future__ import annotations

import uuid
from typing import Final

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import (
    AnalysisStatus,
    ClaimType,
    FactSourceType,
    FactType,
    ProductStatus,
    UploadStatus,
)
from backend_core.domain.models import ProductAnalysis, ProductAsset
from backend_core.errors import AppError, ErrorCode, ValidationError
from backend_core.observability import get_logger
from backend_core.prompts.registry import active_version
from backend_core.providers.base import ProviderImage, VisionAnalysis, VisionProvider
from backend_core.providers.registry import get_vision_provider
from backend_core.providers.schemas import INFERRED_FIELDS, OBSERVED_FIELDS, ProductIntelligence
from backend_core.repositories.products import ProductRepository
from backend_core.services.product_truth import ProductTruthService
from backend_core.services.products import ProductService
from backend_core.storage.s3 import S3ObjectStorage, get_storage

logger = get_logger(__name__)

#: The prompt this service expects the vision provider to use. Recorded before
#: the call so a run that never returns still says what it was going to send;
#: whatever the provider reports afterwards wins.
_PROMPT_KEY: Final[str] = "product_analyze_v1"

#: How each observed field maps into the fact vocabulary. Only fields listed in
#: `OBSERVED_FIELDS` appear here — that is the §109 boundary, expressed as the
#: absence of an entry rather than as a check somebody could forget to run.
_FACT_TYPE_FOR_FIELD: Final[dict[str, FactType]] = {
    "colors": FactType.APPEARANCE,
    "materials": FactType.MATERIAL,
    "visible_text": FactType.OTHER,
    "structural_features": FactType.FEATURE,
    "visual_features": FactType.APPEARANCE,
}


class AnalysisFailedError(AppError):
    """The provider could not produce a usable analysis."""

    code = ErrorCode.PROVIDER_REJECTED
    http_status = 502
    default_message = "The product analysis could not be completed."


class ProductAnalysisService:
    """Runs vision analysis and files what comes back."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: VisionProvider | None = None,
        storage: S3ObjectStorage | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._provider_override = provider
        self._storage_override = storage
        self._repo = ProductRepository(session)
        self._products = ProductService(session)
        self._truth = ProductTruthService(session)

    # Built on first use, not in `__init__`: reading the analysis history must
    # not construct an HTTP client, and must not fail because a vision provider
    # is misconfigured. A read has no business depending on either.

    @property
    def provider(self) -> VisionProvider:
        if self._provider_override is None:
            self._provider_override = get_vision_provider(self._settings)
        return self._provider_override

    @property
    def storage(self) -> S3ObjectStorage:
        if self._storage_override is None:
            self._storage_override = get_storage()
        return self._storage_override

    async def analyze(self, *, workspace_id: uuid.UUID, product_id: uuid.UUID) -> ProductAnalysis:
        """Analyse a product's images and file the results for review.

        Synchronous, as §83 permits for a short task. The product passes
        `ASSETS_READY → ANALYZING → REVIEW_REQUIRED` within one transaction, so
        both edges of §104 are honoured; the intermediate state is not
        separately observable, which is honest for a call that takes seconds.
        When the job system lands (PHASE 9) `ANALYZING` becomes a state a
        client can actually poll.

        On failure the transaction rolls back and the product stays
        `ASSETS_READY` — §24's rule that a failure must not strand an entity
        mid-state.
        """
        product = await self._products.get(workspace_id=workspace_id, product_id=product_id)

        links = await self._repo.list_assets(workspace_id, product_id)
        images, analyzed_ids = await self._load_images(links)
        if not images:
            raise ValidationError(
                "Attach at least one product image before running analysis.",
                details={"product_id": str(product_id)},
            )

        analysis = ProductAnalysis(
            workspace_id=workspace_id,
            product_id=product_id,
            status=AnalysisStatus.PENDING,
            provider=self.provider.name,
            # Provisional: the provider reports what it actually used, and the
            # two are overwritten below. They are written now rather than left
            # null so a run that dies before the provider answers still says
            # which prompt it was about to send (§15).
            prompt_key=_PROMPT_KEY,
            prompt_version=active_version(_PROMPT_KEY),
            # Exactly the assets the provider saw, reported by the loader. Not
            # a slice of `links`: the loader skips video attachments, so the
            # first N links and the N images sent are different sets whenever a
            # product mixes media (§15 wants the inputs of a call recorded
            # accurately, not approximately).
            analyzed_asset_ids=[str(asset_id) for asset_id in analyzed_ids],
        )
        self._session.add(analysis)
        await self._session.flush()

        try:
            # boto3 and most provider SDKs are synchronous; running the call on
            # the event loop would block every other request for its duration.
            result: VisionAnalysis = await to_thread.run_sync(
                lambda: self.provider.analyze_product(
                    images,
                    product_name=product.name,
                    category=product.category,
                )
            )
        except Exception as exc:
            # Recorded, not swallowed: §15 wants the prompt of every call and a
            # failed call is the one most worth explaining. Re-raised so the
            # request fails and the transaction — including the product's
            # status — rolls back.
            analysis.status = AnalysisStatus.FAILED
            analysis.error_code = type(exc).__name__
            analysis.error_message = str(exc)[:2000]
            await self._session.flush()
            logger.warning(
                "product_analysis_failed",
                extra={"product_id": str(product_id), "provider": self.provider.name},
                exc_info=True,
            )
            raise AnalysisFailedError(
                "The product analysis could not be completed. Try again shortly."
            ) from exc

        analysis.prompt_key = result.prompt_key
        analysis.prompt_version = result.prompt_version
        analysis.model = result.usage.model
        analysis.input_tokens = result.usage.input_tokens
        analysis.output_tokens = result.usage.output_tokens
        analysis.latency_ms = result.usage.latency_ms
        analysis.result = result.intelligence.model_dump()
        analysis.status = AnalysisStatus.SUCCEEDED

        facts, claims = await self._persist(workspace_id, product_id, result.intelligence)
        analysis.created_fact_count = facts
        analysis.created_claim_count = claims

        # §14: the analysis is aesthetic direction as well as observation, and
        # visual_dna is creative rather than factual — so it goes on the
        # product directly instead of through fact verification.
        product.visual_dna = result.intelligence.visual_dna.model_dump()
        product.ai_summary = _summarise(result.intelligence)

        await self._products.transition(
            workspace_id=workspace_id, product_id=product_id, target=ProductStatus.ANALYZING
        )
        await self._products.transition(
            workspace_id=workspace_id,
            product_id=product_id,
            target=ProductStatus.REVIEW_REQUIRED,
        )
        await self._session.flush()

        logger.info(
            "product_analysis_succeeded",
            extra={
                "product_id": str(product_id),
                "provider": result.provider,
                "prompt_key": result.prompt_key,
                "prompt_version": result.prompt_version,
                "facts_created": facts,
                "claims_created": claims,
            },
        )
        return analysis

    # -- filing the results ------------------------------------------------

    async def _persist(
        self,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        intelligence: ProductIntelligence,
    ) -> tuple[int, int]:
        """Turn an analysis into facts and claims, keeping the §109 boundary."""
        fact_count = 0
        for field_name, fact_type, value in _fact_specs(intelligence):
            await self._truth.create_fact(
                workspace_id=workspace_id,
                product_id=product_id,
                fact_type=fact_type,
                key=field_name,
                value_text=value,
                # `AI_VISION` forces `AI_INFERRED` inside `create_fact`
                # regardless of anything asked for here — P6-T07's requirement,
                # enforced one layer below this one.
                source_type=FactSourceType.AI_VISION,
            )
            fact_count += 1

        claim_count = 0
        for value in intelligence.possible_selling_points:
            await self._truth.create_claim(
                workspace_id=workspace_id,
                product_id=product_id,
                claim_text=value,
                # The model's own framing is a benefit, not a measurement, so
                # FUNCTIONAL — which still requires a verified fact before it
                # can be approved. Nothing here is publishable as written.
                claim_type=ClaimType.FUNCTIONAL,
                source_fact_ids=[],
            )
            claim_count += 1

        return fact_count, claim_count

    async def _load_images(
        self, links: list[ProductAsset]
    ) -> tuple[list[ProviderImage], list[uuid.UUID]]:
        """Fetch image bytes for the provider, and say which assets they were.

        The provider is handed bytes rather than a URL on purpose: a presigned
        URL to our own bucket is a credential, and handing one to a third party
        both leaks it and makes the call depend on our storage being reachable
        from their network.

        The cap counts *images taken*, not links examined — capping the links
        first would send nothing at all for a product whose first few
        attachments happen to be video.
        """
        limit = self._settings.vision_max_images
        images: list[ProviderImage] = []
        analyzed: list[uuid.UUID] = []

        for link in links:
            if len(images) >= limit:
                break
            media = link.media_asset
            # A PENDING or FAILED upload has no object behind it, or an
            # unverified one; either way it is not evidence about the product.
            if media.upload_status is not UploadStatus.READY:
                continue
            if not media.mime_type.startswith("image/"):
                continue
            data = await to_thread.run_sync(self.storage.get_bytes, media.object_key)
            images.append(
                ProviderImage(
                    data=data,
                    mime_type=media.mime_type,
                    role=link.asset_role.value,
                )
            )
            analyzed.append(media.id)

        return images, analyzed

    # -- reads --------------------------------------------------------------

    async def latest(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> ProductAnalysis | None:
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        return await self._repo.latest_analysis(workspace_id, product_id)

    async def history(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, limit: int = 20
    ) -> list[ProductAnalysis]:
        """Past runs, newest first.

        Goes through `ProductService.get` rather than straight to the
        repository so an unknown or other-workspace product is a 404 here as
        everywhere else — a read that skipped that check would be a tenancy
        leak dressed as a convenience (§60).
        """
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        return await self._repo.list_analyses(workspace_id, product_id, limit=limit)


def _fact_specs(
    intelligence: ProductIntelligence,
) -> list[tuple[str, FactType, str]]:
    """Every observation eligible to become a fact.

    Iterates :data:`OBSERVED_FIELDS`, so the inferred fields cannot appear in
    the result no matter what the provider returned. That is the §109 boundary
    as code rather than as a rule someone has to remember.
    """
    specs: list[tuple[str, FactType, str]] = []
    for field_name in OBSERVED_FIELDS:
        fact_type = _FACT_TYPE_FOR_FIELD[field_name]
        for value in getattr(intelligence, field_name):
            cleaned = str(value).strip()
            if cleaned:
                specs.append((field_name, fact_type, cleaned))
    return specs


def _summarise(intelligence: ProductIntelligence) -> str:
    """A short human-readable digest for the product page.

    Explicitly *not* a source of truth — `Product.ai_summary` exists to be
    read, and anything load-bearing has to exist as a verified fact (§13).
    """
    parts: list[str] = []
    if intelligence.category:
        parts.append(intelligence.category)
    if intelligence.colors:
        parts.append("、".join(intelligence.colors[:3]))
    if intelligence.materials:
        parts.append("、".join(intelligence.materials[:3]))
    if intelligence.uncertain_fields:
        parts.append(f"未确定：{'、'.join(intelligence.uncertain_fields[:4])}")
    return " · ".join(parts)


__all__ = [
    "INFERRED_FIELDS",
    "AnalysisFailedError",
    "ProductAnalysisService",
]
