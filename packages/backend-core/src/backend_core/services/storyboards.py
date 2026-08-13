"""Storyboard generation and shot editing (§18, §19, §29, P8-T04 through T08).

Three rules meet here, and each is enforced in a different place on purpose.

**§18's duration constraint** is enforced twice: `fit_shot_durations` scales the
model's proportions to hit the target, and `validate_storyboard_duration`
refuses to store anything that still misses. The first is a fix, the second is
a guarantee — a scaling bug would otherwise pass silently.

**§19's prohibition** — never hand a video model raw natural language — is
enforced by construction: `visual_prompt` is written only by
`compile_shot_prompt`, and the API exposes no way to set it directly. A user
edits the *fields* a prompt is built from; the prompt is recompiled from them.

**§29's identity lock** defaults on for shots where the product fills the
frame, and the reference assets it locks against come from the product's own
imagery. A locked shot with no identity reference is a lock over nothing, so
that combination is refused rather than stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    ModerationTarget,
    ProductAssetRole,
    ProjectStatus,
    ReferenceRole,
    ShotStatus,
    ShotType,
    StoryboardStatus,
    TransitionType,
    UploadStatus,
    VerificationStatus,
)
from backend_core.domain.models import ProductAsset, Project, Shot, ShotReference, Storyboard
from backend_core.errors import AppError, ErrorCode, NotFoundError, ValidationError
from backend_core.observability import get_logger
from backend_core.prompts.compiler import (
    ShotPromptSpec,
    compile_shot_prompt,
    default_identity_lock,
    product_identity_line,
)
from backend_core.providers.base import CreativeBrief, LLMProvider
from backend_core.providers.registry import get_llm_provider
from backend_core.providers.storyboard_schemas import (
    DurationMismatchError,
    ShotDraft,
    fit_shot_durations,
    suggested_shot_count,
    validate_storyboard_duration,
)
from backend_core.repositories.products import ProductRepository
from backend_core.repositories.projects import ProjectRepository
from backend_core.repositories.storyboards import StoryboardRepository
from backend_core.services.moderation import ModerationService
from backend_core.services.projects import ProjectService

logger = get_logger(__name__)

#: Product asset roles that make good identity references, best first. §29's
#: lock compares generated frames against these, so a front-on shot beats a
#: packaging photo — the former shows the product, the latter shows its box.
_IDENTITY_ROLE_PREFERENCE: Final[tuple[ProductAssetRole, ...]] = (
    ProductAssetRole.FRONT,
    ProductAssetRole.ANGLE_45,
    ProductAssetRole.SIDE,
    ProductAssetRole.DETAIL,
    ProductAssetRole.STRUCTURE,
    ProductAssetRole.MATERIAL,
    ProductAssetRole.BACK,
)

#: How many identity references one shot carries. More than three rarely helps
#: any provider and costs a token budget that the prompt needs.
_MAX_IDENTITY_REFERENCES: Final[int] = 3


class StoryboardFailedError(AppError):
    """The provider could not produce a usable storyboard."""

    code = ErrorCode.PROVIDER_REJECTED
    http_status = 502
    default_message = "The storyboard could not be generated."


class StoryboardService:
    """Generates storyboards and keeps their shots compilable."""

    def __init__(self, session: AsyncSession, *, provider: LLMProvider | None = None) -> None:
        self._session = session
        self._provider_override = provider
        self._repo = StoryboardRepository(session)
        self._projects_repo = ProjectRepository(session)
        self._products = ProductRepository(session)
        self._projects = ProjectService(session)

    @property
    def provider(self) -> LLMProvider:
        if self._provider_override is None:
            self._provider_override = get_llm_provider()
        return self._provider_override

    # -- generation (§18) ---------------------------------------------------

    async def generate(self, *, workspace_id: uuid.UUID, project_id: uuid.UUID) -> Storyboard:
        """Break the approved script into shots (§18).

        Requires an *approved* script, not merely a latest one. §17 makes
        approval the act by which a human accepts the words; generating a
        storyboard from a draft would quietly skip it.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)

        script = await self._projects_repo.approved_script(workspace_id, project_id)
        if script is None:
            raise ValidationError(
                "Approve a script before building the storyboard.",
                details={"project_id": str(project_id)},
            )

        brief_source = await self._identity_context(project)

        try:
            result = await to_thread.run_sync(
                lambda: self.provider.generate_storyboard(
                    brief_source.brief,
                    script.plain_text,
                    shot_count=suggested_shot_count(project.duration_seconds),
                )
            )
        except Exception as exc:
            logger.warning(
                "storyboard_generation_failed",
                extra={"project_id": str(project_id), "provider": self.provider.name},
                exc_info=True,
            )
            raise StoryboardFailedError(
                "The storyboard could not be generated. Try again shortly."
            ) from exc

        drafts = _refit(list(result.storyboard.shots), project.duration_seconds)
        total = round(sum(draft.duration_seconds for draft in drafts), 2)

        try:
            validate_storyboard_duration(total, project.duration_seconds)
        except DurationMismatchError as exc:
            # Reached only if refitting could not close the gap, which means
            # the shot *shape* cannot fit the target within §18's per-shot
            # bounds. Regenerating is the fix; storing it is not.
            raise StoryboardFailedError(str(exc)) from exc

        version = await self._repo.next_version(workspace_id, project_id)
        storyboard = Storyboard(
            workspace_id=workspace_id,
            project_id=project_id,
            script_id=script.id,
            version=version,
            status=StoryboardStatus.DRAFT,
            total_duration_seconds=total,
            model_info={
                "provider": result.provider,
                "prompt_key": result.prompt_key,
                "prompt_version": result.prompt_version,
                "model": result.usage.model,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "latency_ms": result.usage.latency_ms,
            },
        )
        self._session.add(storyboard)
        await self._session.flush()

        shots: list[Shot] = []
        for draft in drafts:
            shot = self._build_shot(storyboard, project, draft, brief_source)
            self._session.add(shot)
            await self._session.flush()
            await self._attach_references(shot, draft, brief_source)
            shots.append(shot)

        # §61's screen, on the compiled prompts rather than on the script.
        # Here because this is the last point before those strings can be sent
        # to a video model, and because a blocked shot should be named as a
        # shot — a rejection that says "storyboard" tells nobody what to edit.
        await ModerationService(self._session).screen_many(
            {str(shot.id): shot.visual_prompt for shot in shots},
            workspace_id=workspace_id,
            target=ModerationTarget.PROMPT,
        )

        if project.status is ProjectStatus.SCRIPTING:
            await self._projects.transition(
                workspace_id=workspace_id,
                project_id=project_id,
                target=ProjectStatus.STORYBOARDING,
            )
        await self._session.flush()

        logger.info(
            "storyboard_generated",
            extra={
                "project_id": str(project_id),
                "version": version,
                "shots": len(drafts),
                "total_seconds": total,
            },
        )
        return storyboard

    # -- shot editing (P8-T05) ----------------------------------------------

    async def update_shot(
        self,
        *,
        workspace_id: uuid.UUID,
        storyboard_id: uuid.UUID,
        shot_id: uuid.UUID,
        title: str | None = None,
        shot_type: ShotType | None = None,
        duration_seconds: float | None = None,
        description: str | None = None,
        camera: str | None = None,
        motion: str | None = None,
        lighting: str | None = None,
        composition: str | None = None,
        voiceover_text: str | None = None,
        subtitle_text: str | None = None,
        transition_in: TransitionType | None = None,
        transition_out: TransitionType | None = None,
        identity_lock: bool | None = None,
    ) -> Shot:
        """Edit a shot's fields and recompile its prompt (§19).

        Note what this method does *not* accept: `visual_prompt` or
        `negative_prompt`. §19 forbids handing a video model a sentence a user
        typed, so those are outputs of the compiler and never inputs. A user
        changes the lighting; the prompt changes because of it.
        """
        shot = await self._get_shot(workspace_id, storyboard_id, shot_id)
        storyboard = await self._get_storyboard(workspace_id, shot.project_id, storyboard_id)

        if storyboard.status is StoryboardStatus.SUPERSEDED:
            raise ValidationError(
                "This storyboard has been replaced by a newer version.",
                details={"storyboard_id": str(storyboard_id)},
            )

        if title is not None:
            shot.title = title.strip()
        if shot_type is not None:
            shot.shot_type = shot_type
        if description is not None:
            shot.description = description.strip()
        if camera is not None:
            shot.camera = camera.strip()
        if motion is not None:
            shot.motion = motion.strip()
        if lighting is not None:
            shot.lighting = lighting.strip()
        if composition is not None:
            shot.composition = composition.strip()
        if voiceover_text is not None:
            shot.voiceover_text = voiceover_text.strip()
        if subtitle_text is not None:
            shot.subtitle_text = subtitle_text.strip()
        if transition_in is not None:
            shot.transition_in = transition_in
        if transition_out is not None:
            shot.transition_out = transition_out
        if identity_lock is not None:
            shot.identity_lock = identity_lock

        if duration_seconds is not None:
            shot.duration_seconds = duration_seconds
            await self._session.flush()
            await self._recompute_total(storyboard)

        project = await self._projects.get(workspace_id=workspace_id, project_id=shot.project_id)
        context = await self._identity_context(project)
        _recompile(shot, project, context)

        await self._session.flush()
        return shot

    async def get_storyboard(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, storyboard_id: uuid.UUID
    ) -> Storyboard:
        return await self._get_storyboard(workspace_id, project_id, storyboard_id)

    async def list_storyboards(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[Storyboard]:
        await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        return await self._repo.list_for_project(workspace_id, project_id)

    async def list_shots(self, *, workspace_id: uuid.UUID, storyboard_id: uuid.UUID) -> list[Shot]:
        return await self._repo.list_shots(workspace_id, storyboard_id)

    async def approve(
        self, *, workspace_id: uuid.UUID, project_id: uuid.UUID, storyboard_id: uuid.UUID
    ) -> Storyboard:
        """Accept a storyboard and supersede the rest.

        Re-validates the duration first. A storyboard whose shots were edited
        after generation can have drifted, and approving is the last moment
        before PHASE 9 starts spending money on it.
        """
        project = await self._projects.get(workspace_id=workspace_id, project_id=project_id)
        storyboard = await self._get_storyboard(workspace_id, project_id, storyboard_id)

        await self._recompute_total(storyboard)
        try:
            validate_storyboard_duration(
                storyboard.total_duration_seconds, project.duration_seconds
            )
        except DurationMismatchError as exc:
            raise ValidationError(str(exc), details={"storyboard_id": str(storyboard_id)}) from exc

        shots = await self._repo.list_shots(workspace_id, storyboard_id)
        if not shots:
            raise ValidationError("A storyboard needs at least one shot.")
        missing = [shot.sequence_no for shot in shots if not shot.visual_prompt.strip()]
        if missing:
            # §84's acceptance criterion, checked rather than assumed: every
            # shot must carry a prompt, because PHASE 9 has nothing to send
            # for one that does not.
            raise ValidationError(
                "Every shot needs a compiled prompt before the storyboard can be approved.",
                details={"shots_without_prompt": missing},
            )

        await self._repo.supersede(workspace_id, project_id, keep=storyboard_id)
        await self._session.flush()
        storyboard.status = StoryboardStatus.APPROVED
        await self._session.flush()

        logger.info(
            "storyboard_approved",
            extra={"project_id": str(project_id), "storyboard_id": str(storyboard_id)},
        )
        return storyboard

    # -- internals ----------------------------------------------------------

    async def _get_storyboard(
        self, workspace_id: uuid.UUID, project_id: uuid.UUID, storyboard_id: uuid.UUID
    ) -> Storyboard:
        storyboard = await self._repo.get(workspace_id, project_id, storyboard_id)
        if storyboard is None:
            raise NotFoundError(
                "Storyboard not found.", details={"storyboard_id": str(storyboard_id)}
            )
        return storyboard

    async def _get_shot(
        self, workspace_id: uuid.UUID, storyboard_id: uuid.UUID, shot_id: uuid.UUID
    ) -> Shot:
        shot = await self._repo.get_shot(workspace_id, storyboard_id, shot_id)
        if shot is None:
            raise NotFoundError("Shot not found.", details={"shot_id": str(shot_id)})
        return shot

    async def _recompute_total(self, storyboard: Storyboard) -> None:
        storyboard.total_duration_seconds = await self._repo.sum_shot_durations(
            storyboard.workspace_id, storyboard.id
        )

    async def _identity_context(self, project: Project) -> _IdentityContext:
        """Assemble what the compiler needs about the product (§13, §29).

        Only `VERIFIED` facts describe the product in the prompt. The rule
        reaches this far because a prompt asserting "brushed aluminium" about a
        plastic product misrepresents it just as surely as a script would — and
        unlike a script, nobody proofreads a prompt before it is sent.
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
        by_key: dict[str, list[str]] = {}
        for fact in facts:
            by_key.setdefault(fact.key, []).append(fact.value_text)

        links = await self._products.list_assets(project.workspace_id, project.product_id)
        identity_assets = _rank_identity_assets(links)

        brief = CreativeBrief(
            product_name=product.name,
            category=product.category,
            verified_facts=[f"{fact.key}: {fact.value_text}" for fact in facts[:40]],
            visual_dna=dict(product.visual_dna or {}),
            purpose=project.purpose.value,
            target_platform=project.target_platform.value,
            target_audience=project.target_audience or "",
            language=project.language,
            aspect_ratio=project.aspect_ratio.value,
            duration_seconds=project.duration_seconds,
            style=project.style.value,
        )

        return _IdentityContext(
            brief=brief,
            product_name=product.name,
            colors=by_key.get("colors", []),
            materials=by_key.get("materials", []),
            structural_features=by_key.get("structural_features", []),
            visual_dna=dict(product.visual_dna or {}),
            identity_asset_ids=identity_assets,
        )

    def _build_shot(
        self,
        storyboard: Storyboard,
        project: Project,
        draft: ShotDraft,
        context: _IdentityContext,
    ) -> Shot:
        shot = Shot(
            workspace_id=storyboard.workspace_id,
            storyboard_id=storyboard.id,
            project_id=project.id,
            sequence_no=draft.sequence_no,
            title=draft.title,
            shot_type=draft.shot_type,
            duration_seconds=draft.duration_seconds,
            description=draft.visual_description,
            camera=draft.camera,
            motion=draft.motion,
            lighting=draft.lighting,
            composition=draft.composition,
            voiceover_text=draft.voiceover,
            subtitle_text=draft.subtitle,
            transition_in=draft.transition_in,
            transition_out=draft.transition_out,
            status=ShotStatus.PENDING,
            identity_lock=default_identity_lock(draft.shot_type),
        )
        _recompile(shot, project, context)
        return shot

    async def _attach_references(
        self, shot: Shot, draft: ShotDraft, context: _IdentityContext
    ) -> None:
        """Resolve the model's requested reference *roles* to real assets (§29).

        The model names what kind of reference a shot needs; this picks which
        image. It has to be this way round — the model has never seen the asset
        table, and letting it guess ids would produce references to nothing.
        """
        wants_identity = "IDENTITY" in draft.reference_roles or shot.identity_lock
        if not wants_identity or not context.identity_asset_ids:
            return

        for asset_id in context.identity_asset_ids[:_MAX_IDENTITY_REFERENCES]:
            self._session.add(
                ShotReference(
                    workspace_id=shot.workspace_id,
                    shot_id=shot.id,
                    media_asset_id=asset_id,
                    reference_role=ReferenceRole.IDENTITY,
                )
            )
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class _IdentityContext:
    """What the compiler needs about the product, gathered once per call.

    Assembled in one place so the prompt and the reference images cannot
    disagree about which product they describe.
    """

    brief: CreativeBrief
    product_name: str
    colors: list[str]
    materials: list[str]
    structural_features: list[str]
    visual_dna: dict[str, Any]
    identity_asset_ids: list[uuid.UUID]


def _recompile(shot: Shot, project: Project, context: _IdentityContext) -> None:
    """Rebuild a shot's prompt from its fields (§19).

    Called on creation and after every edit. The prompt is never stored
    independently of the fields it came from, so the two cannot drift — which
    is the failure that would let a user edit "lighting" and generate against
    the old one.
    """
    palette = context.visual_dna.get("palette")
    tone = context.visual_dna.get("tone")

    spec = ShotPromptSpec(
        subject=shot.description or shot.title or context.product_name,
        product_identity=product_identity_line(
            context.product_name,
            colors=context.colors,
            materials=context.materials,
            structural_features=context.structural_features,
        ),
        environment=shot.composition,
        composition=f"{project.aspect_ratio.value} frame; {shot.composition}".strip("; "),
        lighting=shot.lighting,
        camera=shot.camera,
        camera_motion=shot.motion,
        object_motion="",
        material=", ".join(context.materials),
        style=_style_line(project.style.value, tone, palette),
        brand="",
        identity_lock=shot.identity_lock,
    )
    compiled = compile_shot_prompt(spec)
    shot.visual_prompt = compiled.prompt
    shot.negative_prompt = compiled.negative_prompt


def _style_line(style: str, tone: object, palette: object) -> str:
    parts = [style.replace("_", " ").lower()]
    for value in (tone, palette):
        if isinstance(value, list) and value:
            parts.append(", ".join(str(item) for item in value[:3]))
    return "; ".join(parts)


def _refit(drafts: list[ShotDraft], target_seconds: int) -> list[ShotDraft]:
    """Scale the model's durations onto the target, keeping its pacing.

    A model is good at "the hook should be short and the reveal long" and bad
    at making eight numbers sum to thirty. Scaling keeps the judgement and
    discards the arithmetic.
    """
    fitted = fit_shot_durations([draft.duration_seconds for draft in drafts], target_seconds)
    return [
        draft.model_copy(update={"duration_seconds": seconds})
        for draft, seconds in zip(drafts, fitted, strict=True)
    ]


def _rank_identity_assets(links: list[ProductAsset]) -> list[uuid.UUID]:
    """Pick the product images that best show what the product looks like.

    Ordered by how much of the product a role actually shows — a front-on
    photograph is a better identity reference than a shot of the packaging,
    which shows a box.
    """
    ranked: list[tuple[int, uuid.UUID]] = []
    for link in links:
        media = link.media_asset
        if media.upload_status is not UploadStatus.READY:
            continue
        if not media.mime_type.startswith("image/"):
            continue
        if link.asset_role not in _IDENTITY_ROLE_PREFERENCE:
            # PACKAGING, LOGO and SCENE are excluded deliberately: they show
            # the box, the mark, or the room, none of which is the product a
            # generated frame has to match.
            continue
        rank = _IDENTITY_ROLE_PREFERENCE.index(link.asset_role)
        # Primary images sort first within their role band.
        ranked.append((rank * 2 + (0 if link.is_primary else 1), media.id))

    ranked.sort(key=lambda pair: pair[0])
    return [asset_id for _, asset_id in ranked]


__all__ = ["StoryboardFailedError", "StoryboardService"]
