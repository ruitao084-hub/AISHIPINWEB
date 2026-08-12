"""Creative plans and scripts (§16, §17, §109, P7-T04 through P7-T10).

**P7-T09 is the reason this module is shaped the way it is.** §17 says a script
may not contain an unverified claim, and §109 says only `VERIFIED` claims may
be used. The enforcement is one function — :meth:`CreativeService._brief` —
through which both generators get their inputs, and which obtains claims by
calling the Truth Layer's `get_verified_claims`.

That matters more than it looks. The alternative shape is "load all claims,
filter where status == VERIFIED", and the difference is that a filter is a step
somebody can forget, move, or get subtly wrong, whereas there is no code path
here that can reach an unverified claim at all. The provider is handed strings;
it never sees a `ProductClaim` and so cannot be the thing that leaks one.

Facts get the same treatment for the same reason (§13).
"""

from __future__ import annotations

import uuid
from typing import Final

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    ProjectStatus,
    ScriptStatus,
    VerificationStatus,
)
from backend_core.domain.models import CreativePlan, Project, Script
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.providers.base import CreativeBrief, LLMProvider
from backend_core.providers.creative_schemas import (
    CreativePlanDraft,
    ScriptDocument,
    character_budget,
    duration_fits,
)
from backend_core.providers.registry import get_llm_provider
from backend_core.repositories.products import ProductRepository
from backend_core.repositories.projects import ProjectRepository
from backend_core.services.product_truth import ProductTruthService
from backend_core.services.projects import ProjectService

logger = get_logger(__name__)

#: How many verified facts reach the prompt. A product with two hundred
#: verified facts would produce a prompt in which the important ones are
#: invisible; the cap is a quality decision, not a cost one.
_MAX_FACTS: Final[int] = 40
_MAX_CLAIMS: Final[int] = 20


class GenerationFailedError(AppError):
    """The provider could not produce usable output."""

    code = ErrorCode.PROVIDER_REJECTED
    http_status = 502
    default_message = "The generation could not be completed."


class NoVerifiedContentError(AppError):
    """The product has nothing confirmed to build a video from (§13).

    Not a provider failure and not a bug: it means the review step has not
    happened yet. Its own error type so the UI can say "verify some facts
    first" rather than "something went wrong".
    """

    code = ErrorCode.CLAIM_NOT_VERIFIED
    http_status = 409
    default_message = (
        "This product has no verified facts yet. Confirm at least one before generating."
    )


class CreativeService:
    """Generates creative plans and scripts for a project."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: LLMProvider | None = None,
    ) -> None:
        self._session = session
        self._provider_override = provider
        self._repo = ProjectRepository(session)
        self._products = ProductRepository(session)
        self._projects = ProjectService(session)
        self._truth = ProductTruthService(session)

    @property
    def provider(self) -> LLMProvider:
        """Built on first use, so reads do not construct an HTTP client."""
        if self._provider_override is None:
            self._provider_override = get_llm_provider()
        return self._provider_override

    # -- creative plans (§16, P7-T06) ---------------------------------------

    async def generate_plans(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[CreativePlan]:
        """Produce three creative directions for a project (§16).

        Regenerating is allowed and produces a new `version` rather than
        replacing the old plans — §103 rule 9 keeps history, and a user who
        preferred round one should be able to see it.

        The project ends in `CREATIVE_PLANNING`. It does not advance to
        `SCRIPTING` here: §16 requires the *user* to choose, and moving the
        project on before they have would make the choice look optional.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        brief, _ = await self._brief(project)

        try:
            result = await to_thread.run_sync(lambda: self.provider.generate_creative_plans(brief))
        except Exception as exc:
            logger.warning(
                "creative_generation_failed",
                extra={"project_id": str(project_id), "provider": self.provider.name},
                exc_info=True,
            )
            raise GenerationFailedError(
                "The creative plans could not be generated. Try again shortly."
            ) from exc

        version = await self._repo.next_plan_version(workspace_id, project_id)
        # A new round invalidates the old choice: the user is about to be shown
        # three new options, and a stale `selected` flag pointing at a plan
        # from round one would silently drive the next script.
        await self._repo.clear_plan_selection(workspace_id, project_id)

        model_info = {
            "provider": result.provider,
            "prompt_key": result.prompt_key,
            "prompt_version": result.prompt_version,
            "model": result.usage.model,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "latency_ms": result.usage.latency_ms,
        }

        plans = [
            CreativePlan(
                workspace_id=workspace_id,
                project_id=project_id,
                version=version,
                title=draft.title,
                concept=draft.concept,
                hook=draft.hook,
                core_message=draft.core_message,
                narrative_structure=draft.narrative_structure,
                visual_direction=draft.visual_direction,
                camera_direction=draft.camera_direction,
                music_direction=draft.music_direction,
                ending_cta=draft.ending_cta,
                risk_notes=draft.risk_notes,
                recommended_style=project.style,
                model_info=model_info,
            )
            for draft in result.plans.plans
        ]
        self._session.add_all(plans)
        await self._session.flush()

        if project.status is not ProjectStatus.CREATIVE_PLANNING:
            await self._advance(project, ProjectStatus.CREATIVE_PLANNING)

        logger.info(
            "creative_plans_generated",
            extra={
                "project_id": str(project_id),
                "version": version,
                "provider": result.provider,
                "prompt_version": result.prompt_version,
            },
        )
        return plans

    async def list_plans(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[CreativePlan]:
        await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        return await self._repo.list_plans(workspace_id, project_id)

    async def select_plan(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, plan_id: uuid.UUID
    ) -> CreativePlan:
        """Choose the direction to script (§16, P7-T07).

        Deselect-then-select in one transaction, because a partial unique index
        permits exactly one selected plan — the constraint is what makes "which
        plan did they pick" a question with one answer.
        """
        await self._projects.get(workspace_id=workspace_id, project_id=project_id)

        plan = await self._repo.get_plan(workspace_id, project_id, plan_id)
        if plan is None:
            raise NotFoundError("Creative plan not found.", details={"plan_id": str(plan_id)})

        await self._repo.clear_plan_selection(workspace_id, project_id)
        # Flushed before the new selection so the partial unique index sees the
        # old row cleared; otherwise both rows are momentarily selected.
        await self._session.flush()

        plan.selected = True
        await self._session.flush()

        logger.info(
            "creative_plan_selected",
            extra={"project_id": str(project_id), "plan_id": str(plan_id)},
        )
        return plan

    # -- scripts (§17, P7-T08/T09/T10) --------------------------------------

    async def generate_script(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Script:
        """Write a script from the selected plan (§17).

        Always a new version, never an edit (§17, P7-T10). The new script is
        `DRAFT`; approving it is a separate act, and approving supersedes every
        other version so PHASE 8 has one script to work from.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)

        plan = await self._repo.selected_plan(workspace_id, project_id)
        if plan is None:
            raise ValidationError(
                "Choose a creative plan before generating a script.",
                details={"project_id": str(project_id)},
            )

        brief, claim_ids = await self._brief(project)
        budget = character_budget(project.duration_seconds)
        draft = _draft_from(plan)

        try:
            result = await to_thread.run_sync(
                lambda: self.provider.generate_script(brief, draft, character_budget=budget)
            )
        except Exception as exc:
            logger.warning(
                "script_generation_failed",
                extra={"project_id": str(project_id), "provider": self.provider.name},
                exc_info=True,
            )
            raise GenerationFailedError(
                "The script could not be generated. Try again shortly."
            ) from exc

        return await self._store_script(
            project=project,
            plan=plan,
            document=result.document,
            claim_ids=claim_ids,
            model_info={
                "provider": result.provider,
                "prompt_key": result.prompt_key,
                "prompt_version": result.prompt_version,
                "model": result.usage.model,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "latency_ms": result.usage.latency_ms,
                "character_budget": budget,
            },
        )

    async def revise_script(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        document: ScriptDocument,
    ) -> Script:
        """Save a human's edit as a new version (§17, P7-T10).

        Never an update in place. §17 requires history to survive an edit, and
        a user who edits and then regrets it must be able to see what they had.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        previous = await self._repo.latest_script(workspace_id, project_id)
        if previous is None:
            raise ValidationError(
                "There is no script to revise yet.", details={"project_id": str(project_id)}
            )

        return await self._store_script(
            project=project,
            plan=None,
            document=document,
            # Carried forward rather than recomputed: this text is the user's,
            # and what mattered is which claims were in scope when it was
            # first written.
            claim_ids=[str(value) for value in previous.sourced_claim_ids],
            model_info={"provider": "human", "edited_from_version": previous.version},
            plan_id=previous.creative_plan_id,
        )

    async def list_scripts(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> list[Script]:
        await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        return await self._repo.list_scripts(workspace_id, project_id)

    async def approve_script(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, script_id: uuid.UUID
    ) -> Script:
        """Accept a script and supersede every other version (§17)."""
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)

        script = await self._repo.get_script(workspace_id, project_id, script_id)
        if script is None:
            raise NotFoundError("Script not found.", details={"script_id": str(script_id)})

        await self._repo.supersede_scripts(workspace_id, project_id, keep=script_id)
        await self._session.flush()

        script.status = ScriptStatus.APPROVED
        await self._session.flush()

        if project.status is ProjectStatus.SCRIPTING:
            await self._advance(project, ProjectStatus.STORYBOARDING)

        logger.info(
            "script_approved",
            extra={"project_id": str(project_id), "script_id": str(script_id)},
        )
        return script

    # -- the P7-T09 boundary ------------------------------------------------

    async def _brief(self, project: Project) -> tuple[CreativeBrief, list[str]]:
        """Assemble §16's inputs — verified content only (§13, §109, P7-T09).

        The single place either generator gets its facts and claims. Claims
        come from `get_verified_claims`, which returns `VERIFIED` and nothing
        else; facts are filtered to `VERIFIED` here for the same reason.

        Both lists become plain strings before they reach a provider. That is
        deliberate: a provider cannot check a verification status, so it must
        never be handed something that has one.

        The claim *ids* are returned alongside, not folded into the brief. They
        are what `Script.sourced_claim_ids` records, so a claim withdrawn later
        can be traced to every script that leaned on it — and keeping them out
        of the brief preserves the rule above.
        """
        product = await self._products.get(project.workspace_id, project.product_id)
        if product is None:
            raise NotFoundError(
                "The product this project was built from no longer exists.",
                details={"product_id": str(project.product_id)},
            )

        facts = await self._products.list_facts(
            project.workspace_id,
            project.product_id,
            verification_status=VerificationStatus.VERIFIED,
        )
        claims = await self._truth.get_verified_claims(
            workspace_id=project.workspace_id, product_id=project.product_id
        )

        if not facts:
            # A video built from nothing verified would be a video built from
            # the model's imagination, which is precisely what §13 forbids.
            raise NoVerifiedContentError(
                "This product has no verified facts yet. Confirm at least one on the "
                "product page before generating creative work.",
                details={"product_id": str(project.product_id)},
            )

        used_claims = claims[:_MAX_CLAIMS]
        brief = CreativeBrief(
            product_name=product.name,
            category=product.category,
            verified_facts=[f"{fact.key}: {fact.value_text}" for fact in facts[:_MAX_FACTS]],
            verified_claims=[claim.claim_text for claim in used_claims],
            visual_dna=dict(product.visual_dna or {}),
            brand_notes=product.description or "",
            purpose=project.purpose.value,
            target_platform=project.target_platform.value,
            target_audience=project.target_audience or "",
            language=project.language,
            aspect_ratio=project.aspect_ratio.value,
            duration_seconds=project.duration_seconds,
            style=project.style.value,
        )
        return brief, [str(claim.id) for claim in used_claims]

    # -- helpers ------------------------------------------------------------

    async def _store_script(
        self,
        *,
        project: Project,
        plan: CreativePlan | None,
        document: ScriptDocument,
        claim_ids: list[str],
        model_info: dict[str, object],
        plan_id: uuid.UUID | None = None,
    ) -> Script:
        version = await self._repo.next_script_version(project.workspace_id, project.id)
        estimated = document.estimated_duration_seconds()

        if not duration_fits(estimated, project.duration_seconds):
            # Recorded, not refused. §17 asks for a *budget*, and a script that
            # lands 40% long is something a human should see and cut — throwing
            # it away would discard work they might prefer to trim themselves.
            logger.info(
                "script_duration_outside_budget",
                extra={
                    "project_id": str(project.id),
                    "estimated_seconds": estimated,
                    "target_seconds": project.duration_seconds,
                },
            )

        script = Script(
            workspace_id=project.workspace_id,
            project_id=project.id,
            creative_plan_id=plan.id if plan is not None else plan_id,
            version=version,
            content_json=document.model_dump(),
            plain_text=document.plain_text,
            status=ScriptStatus.DRAFT,
            sourced_claim_ids=claim_ids,
            estimated_duration_seconds=estimated,
            model_info=dict(model_info),
        )
        self._session.add(script)
        await self._session.flush()

        if project.status is ProjectStatus.CREATIVE_PLANNING:
            await self._advance(project, ProjectStatus.SCRIPTING)

        logger.info(
            "script_generated",
            extra={
                "project_id": str(project.id),
                "version": version,
                "estimated_seconds": estimated,
                "sourced_claims": len(claim_ids),
            },
        )
        return script

    async def _advance(self, project: Project, target: ProjectStatus) -> None:
        await self._projects.transition(
            workspace_id=project.workspace_id, project_id=project.id, target=target
        )


def _draft_from(plan: CreativePlan) -> CreativePlanDraft:
    """The stored plan, back in the shape a provider expects."""
    return CreativePlanDraft(
        title=plan.title,
        concept=plan.concept,
        hook=plan.hook,
        core_message=plan.core_message,
        narrative_structure=plan.narrative_structure,
        visual_direction=plan.visual_direction,
        camera_direction=plan.camera_direction,
        music_direction=plan.music_direction,
        ending_cta=plan.ending_cta,
        risk_notes=plan.risk_notes,
    )


__all__ = [
    "CreativeService",
    "GenerationFailedError",
    "NoVerifiedContentError",
]
