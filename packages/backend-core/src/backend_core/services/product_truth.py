"""The Product Truth Layer (taskbook §13, §109, P5-T04 … P5-T07).

This module exists to make one guarantee hold: **the platform never states a
product fact that a human has not confirmed.**

§13's worked example is the whole brief. Given an air purifier, a model will
happily write "removes 99.9% of formaldehyde" — a number it invented, about a
real product, that a real company would then broadcast. What it may write is
"helps filter impurities and odours from the air", and *only* if that function
has been confirmed as a fact.

Three rules implement that, and each is enforced here rather than trusted to a
caller:

1. **Anything the AI produces starts at `AI_INFERRED`.** :meth:`create_fact`
   overrides the requested status for AI-sourced facts. A caller cannot insert
   a pre-verified AI fact even by asking.
2. **Promotion to `VERIFIED` requires a person**, and records which person and
   when. The database refuses a `VERIFIED` row with no timestamp.
3. **A claim that asserts anything checkable needs verified facts behind it**,
   and stops being verified the moment that evidence is withdrawn.

The third is the one that decays silently if nobody implements it. Verifying a
claim against a fact and then rejecting the fact leaves, in a naive design, a
`VERIFIED` claim citing withdrawn evidence — which is exactly the fabricated
statement the layer exists to prevent, arrived at one step at a time. So
rejecting or editing a fact demotes every claim that cited it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    ClaimRiskLevel,
    ClaimStatus,
    ClaimType,
    FactSourceType,
    FactType,
    ProductStatus,
    VerificationStatus,
    default_risk_level,
    requires_fact_backing,
)
from backend_core.domain.models import ProductClaim, ProductFact, User
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.repositories.products import ProductRepository
from backend_core.services.products import ProductService

logger = get_logger(__name__)

#: Sources whose output is a machine's guess, never evidence (§13).
_AI_SOURCES: frozenset[FactSourceType] = frozenset(
    {FactSourceType.AI_VISION, FactSourceType.AI_TEXT}
)


class UnsupportedClaimError(AppError):
    """The claim cannot be verified because its evidence does not support it."""

    code = ErrorCode.CLAIM_NOT_VERIFIED
    http_status = 409
    default_message = "This claim cannot be verified without at least one verified supporting fact."


class ProductTruthService:
    """Facts, claims, and the verification rules that connect them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ProductRepository(session)
        self._products = ProductService(session)

    # -- facts -------------------------------------------------------------

    async def create_fact(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        fact_type: FactType,
        key: str,
        value_text: str,
        source_type: FactSourceType,
        value_json: dict[str, Any] | None = None,
        source_asset_id: uuid.UUID | None = None,
        verified_by: User | None = None,
    ) -> ProductFact:
        """Record a fact, at the strongest status its source justifies.

        The status is *derived*, never accepted from the caller:

        * An AI source is always `AI_INFERRED`, whatever else was asked for.
          This is §13's central rule and P6-T07 restates it — the analyser in
          PHASE 6 calls this method, and it must not be able to smuggle a
          verified fact into the database.
        * A user supplying a fact and confirming it in the same action gets
          `VERIFIED` with attribution.
        * Otherwise `USER_PROVIDED`: entered by a person, not yet confirmed.
        """
        await self._products.get(workspace_id=workspace_id, product_id=product_id)

        if source_type in _AI_SOURCES:
            status = VerificationStatus.AI_INFERRED
            verifier = None
        elif verified_by is not None:
            status = VerificationStatus.VERIFIED
            verifier = verified_by
        else:
            status = VerificationStatus.USER_PROVIDED
            verifier = None

        if source_asset_id is not None:
            asset = await self._repo.get_media_asset(workspace_id, source_asset_id)
            if asset is None:
                raise NotFoundError("That source asset does not exist.")

        fact = await self._repo.create_fact(
            workspace_id=workspace_id,
            product_id=product_id,
            fact_type=fact_type,
            key=key,
            value_text=value_text,
            value_json=value_json,
            source_type=source_type,
            source_asset_id=source_asset_id,
            verification_status=status,
            verified_by_user_id=verifier.id if verifier else None,
            verified_at=datetime.now(UTC) if verifier else None,
        )

        logger.info(
            "product_fact_created",
            extra={
                "product_id": str(product_id),
                "fact_id": str(fact.id),
                "source_type": source_type.value,
                "verification_status": fact.verification_status.value,
            },
        )
        return fact

    async def list_facts(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        verification_status: VerificationStatus | None = None,
    ) -> list[ProductFact]:
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        return await self._repo.list_facts(
            workspace_id, product_id, verification_status=verification_status
        )

    async def get_verified_facts(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> list[ProductFact]:
        """Only facts a person has confirmed.

        The accessor generation code is expected to use. Reaching for
        :meth:`list_facts` and filtering by hand is how an `AI_INFERRED` value
        ends up in a script.
        """
        return await self._repo.list_facts(
            workspace_id, product_id, verification_status=VerificationStatus.VERIFIED
        )

    async def verify_fact(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID, user: User
    ) -> ProductFact:
        """Confirm a fact, recording who did it and when (§13)."""
        fact = await self._get_fact(workspace_id, product_id, fact_id)
        _stamp_verified(fact, user)
        await self._session.flush()

        logger.info(
            "product_fact_verified",
            extra={"product_id": str(product_id), "fact_id": str(fact_id)},
        )
        return fact

    async def reject_fact(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID, user: User
    ) -> ProductFact:
        """Mark a fact wrong, and withdraw anything that relied on it.

        Rejected rather than deleted: the analyser should not re-propose a
        value a human has already refused, and the review history is worth
        keeping (§13).
        """
        fact = await self._get_fact(workspace_id, product_id, fact_id)
        fact.verification_status = VerificationStatus.REJECTED
        fact.verified_by_user_id = user.id
        fact.verified_at = datetime.now(UTC)
        await self._session.flush()

        demoted = await self._demote_claims_citing(workspace_id, product_id, fact_id)

        logger.info(
            "product_fact_rejected",
            extra={
                "product_id": str(product_id),
                "fact_id": str(fact_id),
                "claims_demoted": demoted,
            },
        )
        return fact

    async def update_fact(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        fact_id: uuid.UUID,
        user: User,
        fact_type: FactType | None = None,
        key: str | None = None,
        value_text: str | None = None,
        value_json: dict[str, Any] | None = None,
        verify: bool = False,
    ) -> ProductFact:
        """Edit a fact, optionally confirming it in the same action.

        Changing the **value** invalidates any prior verification: a claim was
        verified against what the fact said at the time, and "removes 99.9%"
        becoming "removes 50%" makes every claim citing it wrong. So a value
        edit drops the fact to `USER_PROVIDED` and demotes dependent claims,
        unless the caller re-confirms it in the same request.

        Editing only the type or key is bookkeeping and leaves verification
        alone — the assertion itself has not changed.
        """
        fact = await self._get_fact(workspace_id, product_id, fact_id)

        value_changed = (value_text is not None and value_text.strip() != fact.value_text) or (
            value_json is not None and value_json != fact.value_json
        )

        if fact_type is not None:
            fact.fact_type = fact_type
        if key is not None:
            fact.key = key.strip()
        if value_text is not None:
            fact.value_text = value_text.strip()
        if value_json is not None:
            fact.value_json = value_json

        if verify:
            _stamp_verified(fact, user)
        elif value_changed and fact.verification_status is VerificationStatus.VERIFIED:
            fact.verification_status = VerificationStatus.USER_PROVIDED
            fact.verified_by_user_id = None
            fact.verified_at = None

        await self._session.flush()

        # Even a re-verified fact demotes its claims: the evidence changed, so
        # a human should look at the sentences built on it again.
        if value_changed:
            await self._demote_claims_citing(workspace_id, product_id, fact_id)

        return fact

    # -- claims ------------------------------------------------------------

    async def create_claim(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        claim_text: str,
        claim_type: ClaimType,
        source_fact_ids: list[uuid.UUID] | None = None,
        risk_level: ClaimRiskLevel | None = None,
    ) -> ProductClaim:
        """Propose a claim. Always `SUGGESTED`, never verified on creation.

        Even a claim a user typed themselves starts unverified: §13 requires a
        confirmation step, and creating-and-confirming in one motion would make
        the review a formality that could be skipped by a client.
        """
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        fact_ids = source_fact_ids or []

        # Cited facts must belong to this product. Without the check a claim
        # could point at another product's — or another tenant's — verified
        # fact and look substantiated.
        if fact_ids:
            found = await self._repo.get_facts_by_ids(workspace_id, product_id, fact_ids)
            if len(found) != len(set(fact_ids)):
                raise ValidationError(
                    "Some cited facts do not belong to this product.",
                    details={"product_id": str(product_id)},
                )

        claim = await self._repo.create_claim(
            workspace_id=workspace_id,
            product_id=product_id,
            claim_text=claim_text,
            claim_type=claim_type,
            source_fact_ids=fact_ids,
            risk_level=risk_level or default_risk_level(claim_type),
        )
        logger.info(
            "product_claim_created",
            extra={
                "product_id": str(product_id),
                "claim_id": str(claim.id),
                "claim_type": claim_type.value,
                "risk_level": claim.risk_level.value,
            },
        )
        return claim

    async def list_claims(
        self,
        *,
        workspace_id: uuid.UUID,
        product_id: uuid.UUID,
        status: ClaimStatus | None = None,
    ) -> list[ProductClaim]:
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        return await self._repo.list_claims(workspace_id, product_id, status=status)

    async def get_verified_claims(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> list[ProductClaim]:
        """§109's accessor: the only claims a script may use.

        PHASE 7's script generator calls exactly this. It returns `VERIFIED`
        claims and nothing else, so there is no filtering step for a caller to
        forget — which is the difference between a rule and a convention.
        """
        return await self._repo.list_claims(workspace_id, product_id, status=ClaimStatus.VERIFIED)

    async def verify_claim(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, claim_id: uuid.UUID, user: User
    ) -> ProductClaim:
        """Approve a claim for use, if its evidence holds up (§13, §109).

        Refused when the claim asserts something checkable and its cited facts
        are missing, unverified or rejected. `EMOTIONAL` claims are exempt
        because they assert nothing that could be substantiated.
        """
        claim = await self._get_claim(workspace_id, product_id, claim_id)

        if requires_fact_backing(claim.claim_type):
            await self._require_verified_backing(workspace_id, product_id, claim)

        claim.status = ClaimStatus.VERIFIED
        claim.verified_by_user_id = user.id
        claim.verified_at = datetime.now(UTC)
        await self._session.flush()

        logger.info(
            "product_claim_verified",
            extra={
                "product_id": str(product_id),
                "claim_id": str(claim_id),
                "claim_type": claim.claim_type.value,
            },
        )
        return claim

    async def reject_claim(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID, claim_id: uuid.UUID, user: User
    ) -> ProductClaim:
        claim = await self._get_claim(workspace_id, product_id, claim_id)
        claim.status = ClaimStatus.REJECTED
        claim.verified_by_user_id = user.id
        claim.verified_at = datetime.now(UTC)
        await self._session.flush()

        logger.info(
            "product_claim_rejected",
            extra={"product_id": str(product_id), "claim_id": str(claim_id)},
        )
        return claim

    async def _require_verified_backing(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, claim: ProductClaim
    ) -> None:
        """Refuse a claim whose evidence does not actually support it."""
        cited = [uuid.UUID(value) for value in claim.source_fact_ids]
        if not cited:
            raise UnsupportedClaimError(
                "This claim states something about the product, so it needs at least "
                "one verified fact behind it before it can be used.",
                details={"claim_type": claim.claim_type.value},
            )

        facts = await self._repo.get_facts_by_ids(workspace_id, product_id, cited)
        verified = [fact for fact in facts if fact.is_verified]
        if not verified:
            raise UnsupportedClaimError(
                "None of the facts this claim cites have been verified.",
                details={
                    "claim_type": claim.claim_type.value,
                    "cited_facts": len(cited),
                    "verified_facts": 0,
                },
            )

    # -- readiness ---------------------------------------------------------

    async def mark_reviewed(
        self, *, workspace_id: uuid.UUID, product_id: uuid.UUID
    ) -> ProductStatus:
        """Move a reviewed product to `READY`, if it has anything to say.

        A product with no verified facts has nothing a script could truthfully
        use, so it is not `READY` no matter how many images it has — §13's
        "if there are not enough verified claims" case.
        """
        product = await self._products.get(workspace_id=workspace_id, product_id=product_id)
        verified = await self.get_verified_facts(workspace_id=workspace_id, product_id=product_id)
        if not verified:
            raise ValidationError(
                "Verify at least one product fact before marking this product ready.",
                details={"verified_facts": 0},
            )
        await self._products.transition(
            workspace_id=workspace_id, product_id=product.id, target=ProductStatus.READY
        )
        return ProductStatus.READY

    # -- internals ---------------------------------------------------------

    async def _demote_claims_citing(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID
    ) -> int:
        """Send every verified claim citing ``fact_id`` back for review.

        Returns how many were demoted, which the caller logs — a silent
        cascade is hard to trust and harder to debug.
        """
        affected = await self._repo.list_verified_claims_citing(workspace_id, product_id, fact_id)
        for claim in affected:
            claim.status = ClaimStatus.SUGGESTED
            claim.verified_by_user_id = None
            claim.verified_at = None
        if affected:
            await self._session.flush()
            logger.info(
                "product_claims_demoted",
                extra={
                    "product_id": str(product_id),
                    "fact_id": str(fact_id),
                    "count": len(affected),
                },
            )
        return len(affected)

    async def _get_fact(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, fact_id: uuid.UUID
    ) -> ProductFact:
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        fact = await self._repo.get_fact(workspace_id, product_id, fact_id)
        if fact is None:
            raise NotFoundError("That fact does not exist.")
        return fact

    async def _get_claim(
        self, workspace_id: uuid.UUID, product_id: uuid.UUID, claim_id: uuid.UUID
    ) -> ProductClaim:
        await self._products.get(workspace_id=workspace_id, product_id=product_id)
        claim = await self._repo.get_claim(workspace_id, product_id, claim_id)
        if claim is None:
            raise NotFoundError("That claim does not exist.")
        return claim


def _stamp_verified(fact: ProductFact, user: User) -> None:
    """Set the three fields that together mean "a person confirmed this".

    Kept in one function because the database rejects a `VERIFIED` fact with no
    timestamp — the constraint exists precisely so a partial version of this
    cannot be written from somewhere else.
    """
    fact.verification_status = VerificationStatus.VERIFIED
    fact.verified_by_user_id = user.id
    fact.verified_at = datetime.now(UTC)
