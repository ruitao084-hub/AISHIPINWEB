"""Brand kits and templates (§57, §58, PHASE 17).

Two features that look like CRUD and are not. The CRUD is the boring part; the
interesting part is what *applying* one does, and both taskbook sections are
explicit that a shallow implementation is the failure mode:

    §58: 禁止把 Brand Kit 只当作 LOGO 上传功能。
    §57: 用户套模板后：根据 Product 动态实例化 Storyboard。

So `apply_brand_kit` returns instructions that reach the writer and the prompt
compiler — tone, required and banned phrases, an ending, visual direction —
not a logo id. And `instantiate` turns a template's blueprint into shots
*against a specific product*, so the same template applied to two products
produces two different storyboards rather than the same one twice.

The banned-phrase check is the one thing here that refuses work. A script
containing a phrase legal has forbidden is a problem regardless of how good the
video is, and catching it after render means paying for the render first.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.domain.enums import (
    HEX_COLOR_PATTERN,
    AspectRatio,
    BrandTone,
    LogoPosition,
    ProjectPurpose,
    ShotType,
    TargetPlatform,
    TemplateCategory,
    TransitionType,
    VideoStyle,
)
from backend_core.domain.models import BrandKit, Product, Project, Template
from backend_core.errors import NotFoundError, ValidationError
from backend_core.observability import get_logger

logger = get_logger(__name__)

_HEX: Final[re.Pattern[str]] = re.compile(HEX_COLOR_PATTERN)

#: §18's per-shot bounds, repeated here so a template is rejected when it is
#: written rather than when a project made from it fails to fit.
MIN_SLOT_SECONDS: Final[float] = 2.0
MAX_SLOT_SECONDS: Final[float] = 10.0
MAX_SLOTS: Final[int] = 24


# --- template blueprint (§57, P17-T03) --------------------------------------


class BlueprintSlot(BaseModel):
    """One shot's shape, before a product fills it in (§57)."""

    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1)
    shot_type: ShotType
    duration_seconds: float = Field(ge=MIN_SLOT_SECONDS, le=MAX_SLOT_SECONDS)

    #: What this shot is *for*, in words. Not a prompt — §19 forbids raw
    #: natural language reaching a video model, so this is an input to the
    #: compiler alongside the product's own facts.
    intent: str = Field(min_length=1, max_length=500)

    camera: str = Field(default="", max_length=200)
    motion: str = Field(default="", max_length=200)
    lighting: str = Field(default="", max_length=200)
    composition: str = Field(default="", max_length=200)

    #: Whether §29's identity lock applies. Defaults to true because most
    #: template slots show the product, and a slot that does not can say so.
    identity_lock: bool = True

    #: A narration line with `{product}`-style placeholders, filled from the
    #: product at instantiation. Optional: a purely visual slot is legitimate.
    narration_template: str = Field(default="", max_length=500)


class TemplateBlueprint(BaseModel):
    """A template's whole shot plan, validated as one thing (§57)."""

    model_config = ConfigDict(extra="forbid")

    slots: list[BlueprintSlot] = Field(min_length=1, max_length=MAX_SLOTS)

    @field_validator("slots")
    @classmethod
    def _sequential(cls, slots: list[BlueprintSlot]) -> list[BlueprintSlot]:
        """Sequence numbers must be 1..n with no gaps.

        Checked because the blueprint is the shot *order*, and a gap makes
        "which shot is third" ambiguous at exactly the moment §33 needs it not
        to be.
        """
        numbers = sorted(slot.sequence_no for slot in slots)
        if numbers != list(range(1, len(slots) + 1)):
            raise ValueError("Blueprint slots must be numbered 1..n with no gaps.")
        return slots

    @property
    def total_seconds(self) -> float:
        return round(sum(slot.duration_seconds for slot in self.slots), 2)


# --- what applying a brand kit produces (§58, P17-T02) ----------------------


@dataclass(frozen=True, slots=True)
class BrandDirectives:
    """A brand kit, in the form the generators consume (§58).

    Deliberately not the model. A `BrandKit` row has a logo asset id and
    timestamps; a writer needs a tone and a list of phrases. Converting once,
    here, is what keeps §58's "not just a logo uploader" true in the places
    that matter — the creative service and the prompt compiler both take this,
    and neither has to know what a `BrandKit` row looks like.
    """

    tone: BrandTone
    required_phrases: tuple[str, ...] = ()
    banned_phrases: tuple[str, ...] = ()
    ending_line: str | None = None
    ending_cta: str | None = None
    visual_guidelines: str | None = None
    subtitle_color: str | None = None
    font_family: str | None = None
    logo_asset_id: uuid.UUID | None = None
    logo_position: LogoPosition = LogoPosition.NONE

    @classmethod
    def of(cls, kit: BrandKit | None) -> BrandDirectives:
        """Build directives, or the neutral set when no kit is bound.

        A project with no brand kit is normal, not an error — §58's features
        are additive. The neutral set is what makes every call site free of
        `if kit is not None`.
        """
        if kit is None:
            return cls(tone=BrandTone.PROFESSIONAL)
        return cls(
            tone=kit.tone,
            required_phrases=tuple(kit.required_phrases),
            banned_phrases=tuple(kit.banned_phrases),
            ending_line=kit.ending_line,
            ending_cta=kit.ending_cta,
            visual_guidelines=kit.visual_guidelines,
            subtitle_color=kit.subtitle_color,
            font_family=kit.font_family,
            logo_asset_id=kit.logo_asset_id,
            logo_position=kit.logo_position,
        )

    def creative_instructions(self) -> list[str]:
        """Lines appended to §16's and §17's briefs.

        Phrased as instructions to a writer rather than as data, because that
        is what they are: "use this tone", "these words must appear". A model
        given a JSON blob of brand fields tends to describe the brand instead
        of speaking as it.
        """
        lines = [f"Write in a {self.tone.value.lower()} tone."]
        if self.required_phrases:
            lines.append(
                "These exact phrases must appear somewhere in the script: "
                + "; ".join(self.required_phrases)
            )
        if self.banned_phrases:
            lines.append("Never use these words or phrases: " + "; ".join(self.banned_phrases))
        if self.ending_line:
            lines.append(f"End with this line: {self.ending_line}")
        if self.ending_cta:
            lines.append(f"The call to action is: {self.ending_cta}")
        if self.visual_guidelines:
            lines.append(f"Visual direction: {self.visual_guidelines}")
        return lines

    def violations(self, text: str) -> list[str]:
        """Banned phrases present in `text` (§58).

        Case-insensitive substring, not word-boundary: a brand that bans
        "cheap" means it in "cheaper" too, and a check that missed the
        inflection would pass a script legal would reject.
        """
        lowered = text.lower()
        return [phrase for phrase in self.banned_phrases if phrase.lower() in lowered]

    def missing_required(self, text: str) -> list[str]:
        """Required phrases absent from `text`.

        Reported rather than inserted. Appending a tagline to a script the
        model wrote produces a sentence nobody would say; telling the writer it
        is required, and telling a reviewer when it is missing, does not.
        """
        lowered = text.lower()
        return [phrase for phrase in self.required_phrases if phrase.lower() not in lowered]


@dataclass(frozen=True, slots=True)
class InstantiatedShot:
    """One shot, produced by applying a template to a product (§57)."""

    sequence_no: int
    shot_type: ShotType
    duration_seconds: float
    description: str
    camera: str = ""
    motion: str = ""
    lighting: str = ""
    composition: str = ""
    voiceover_text: str = ""
    identity_lock: bool = True
    extra_prompt_rules: tuple[str, ...] = field(default=())


class BrandingService:
    """CRUD for brand kits and templates, plus what applying them means."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- brand kits (§58, P17-T01) ------------------------------------------

    async def create_brand_kit(
        self, *, workspace_id: uuid.UUID, name: str, **fields: Any
    ) -> BrandKit:
        _validate_colors(fields)
        kit = BrandKit(workspace_id=workspace_id, name=name, **fields)

        if kit.is_default:
            await self._clear_default(workspace_id)

        self._session.add(kit)
        await self._session.flush()
        logger.info("brand_kit_created", extra={"brand_kit_id": str(kit.id)})
        return kit

    async def list_brand_kits(self, *, workspace_id: uuid.UUID) -> list[BrandKit]:
        result = await self._session.execute(
            select(BrandKit)
            .where(BrandKit.workspace_id == workspace_id, BrandKit.deleted_at.is_(None))
            .order_by(BrandKit.is_default.desc(), BrandKit.name)
        )
        return list(result.scalars().all())

    async def get_brand_kit(self, *, workspace_id: uuid.UUID, brand_kit_id: uuid.UUID) -> BrandKit:
        result = await self._session.execute(
            select(BrandKit).where(
                BrandKit.id == brand_kit_id,
                BrandKit.workspace_id == workspace_id,
                BrandKit.deleted_at.is_(None),
            )
        )
        kit = result.scalar_one_or_none()
        if kit is None:
            raise NotFoundError("Brand kit not found.", details={"brand_kit_id": str(brand_kit_id)})
        return kit

    async def update_brand_kit(
        self, *, workspace_id: uuid.UUID, brand_kit_id: uuid.UUID, **fields: Any
    ) -> BrandKit:
        kit = await self.get_brand_kit(workspace_id=workspace_id, brand_kit_id=brand_kit_id)
        _validate_colors(fields)

        if fields.get("is_default") is True:
            await self._clear_default(workspace_id, except_id=kit.id)

        for key, value in fields.items():
            if value is not None:
                setattr(kit, key, value)
        await self._session.flush()
        return kit

    async def delete_brand_kit(self, *, workspace_id: uuid.UUID, brand_kit_id: uuid.UUID) -> None:
        """Soft-delete. Projects that used it keep their reference.

        Hard-deleting would either orphan or cascade away finished projects,
        and "which brand was this video made under" is a question somebody asks
        about videos that already shipped.
        """
        kit = await self.get_brand_kit(workspace_id=workspace_id, brand_kit_id=brand_kit_id)
        kit.deleted_at = datetime.now(UTC)
        kit.is_default = False
        await self._session.flush()

    async def directives_for(self, project: Project) -> BrandDirectives:
        """The brand rules in force for a project (§58, P17-T02).

        Falls back to the workspace default when a project names no kit, so
        binding a brand once applies to everything made afterwards without
        editing each project.
        """
        if project.brand_kit_id is not None:
            kit = await self.get_brand_kit(
                workspace_id=project.workspace_id, brand_kit_id=project.brand_kit_id
            )
            return BrandDirectives.of(kit)

        result = await self._session.execute(
            select(BrandKit).where(
                BrandKit.workspace_id == project.workspace_id,
                BrandKit.is_default.is_(True),
                BrandKit.deleted_at.is_(None),
            )
        )
        return BrandDirectives.of(result.scalar_one_or_none())

    async def _clear_default(
        self, workspace_id: uuid.UUID, *, except_id: uuid.UUID | None = None
    ) -> None:
        """Demote the current default before promoting a new one.

        Needed because the partial unique index would otherwise refuse the
        insert, and a constraint violation is a worse message than a swap.
        """
        result = await self._session.execute(
            select(BrandKit).where(
                BrandKit.workspace_id == workspace_id,
                BrandKit.is_default.is_(True),
                BrandKit.deleted_at.is_(None),
            )
        )
        for existing in result.scalars().all():
            if except_id is None or existing.id != except_id:
                existing.is_default = False
        await self._session.flush()

    # -- templates (§57, P17-T04) -------------------------------------------

    async def create_template(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        category: TemplateCategory,
        aspect_ratio: AspectRatio,
        duration_seconds: int,
        style: VideoStyle,
        purpose: ProjectPurpose,
        target_platform: TargetPlatform,
        blueprint: TemplateBlueprint,
        **fields: Any,
    ) -> Template:
        """Store a template, refusing a blueprint that cannot fit its duration.

        The duration check is here rather than at instantiation because a
        template is written once and used many times: catching it now costs the
        author one message, catching it later costs every user of the template
        a failed project.
        """
        drift = abs(blueprint.total_seconds - duration_seconds)
        if drift > max(2.0, duration_seconds * 0.2):
            raise ValidationError(
                "The blueprint's shots do not add up to the template's duration.",
                details={
                    "blueprint_seconds": blueprint.total_seconds,
                    "template_seconds": duration_seconds,
                },
            )

        template = Template(
            workspace_id=workspace_id,
            name=name,
            category=category,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            style=style,
            purpose=purpose,
            target_platform=target_platform,
            storyboard_blueprint=[slot.model_dump(mode="json") for slot in blueprint.slots],
            **fields,
        )
        self._session.add(template)
        await self._session.flush()
        logger.info("template_created", extra={"template_id": str(template.id)})
        return template

    async def list_templates(
        self,
        *,
        workspace_id: uuid.UUID,
        category: TemplateCategory | None = None,
        include_presets: bool = True,
    ) -> list[Template]:
        """§57's gallery (P17-T06).

        Presets are returned alongside the workspace's own. They live in other
        workspaces' rows but are readable by everyone, which is what avoids
        copying the platform's template library into every tenant.
        """
        query = select(Template).where(Template.deleted_at.is_(None))
        if include_presets:
            query = query.where(
                (Template.workspace_id == workspace_id) | (Template.is_preset.is_(True))
            )
        else:
            query = query.where(Template.workspace_id == workspace_id)
        if category is not None:
            query = query.where(Template.category == category)

        result = await self._session.execute(
            query.order_by(Template.is_preset.desc(), Template.usage_count.desc(), Template.name)
        )
        return list(result.scalars().all())

    async def get_template(self, *, workspace_id: uuid.UUID, template_id: uuid.UUID) -> Template:
        result = await self._session.execute(
            select(Template).where(
                Template.id == template_id,
                Template.deleted_at.is_(None),
                # A preset is readable from any workspace; anything else is not.
                (Template.workspace_id == workspace_id) | (Template.is_preset.is_(True)),
            )
        )
        template = result.scalar_one_or_none()
        if template is None:
            raise NotFoundError("Template not found.", details={"template_id": str(template_id)})
        return template

    async def update_template(
        self,
        *,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        blueprint: TemplateBlueprint | None = None,
        **fields: Any,
    ) -> Template:
        template = await self.get_template(workspace_id=workspace_id, template_id=template_id)
        if template.is_preset:
            raise ValidationError(
                "Presets cannot be edited. Duplicate it first.",
                details={"template_id": str(template_id)},
            )
        if blueprint is not None:
            template.storyboard_blueprint = [
                slot.model_dump(mode="json") for slot in blueprint.slots
            ]
        for key, value in fields.items():
            if value is not None:
                setattr(template, key, value)
        await self._session.flush()
        return template

    async def duplicate_template(
        self, *, workspace_id: uuid.UUID, template_id: uuid.UUID, name: str | None = None
    ) -> Template:
        """Copy a template into this workspace as an editable one.

        The only way to build on a preset, which is otherwise read-only
        everywhere. The copy is not a preset and its usage count starts at
        zero — inheriting the original's popularity would make the gallery's
        ordering meaningless within a week.
        """
        source = await self.get_template(workspace_id=workspace_id, template_id=template_id)
        copy = Template(
            workspace_id=workspace_id,
            name=name or f"{source.name} (copy)",
            category=source.category,
            description=source.description,
            is_preset=False,
            aspect_ratio=source.aspect_ratio,
            duration_seconds=source.duration_seconds,
            style=source.style,
            purpose=source.purpose,
            target_platform=source.target_platform,
            storyboard_blueprint=list(source.storyboard_blueprint),
            prompt_rules=list(source.prompt_rules),
            subtitle_style=dict(source.subtitle_style),
            transition_style=source.transition_style,
            music_tags=list(source.music_tags),
            ending_style=source.ending_style,
        )
        self._session.add(copy)
        await self._session.flush()
        return copy

    async def delete_template(self, *, workspace_id: uuid.UUID, template_id: uuid.UUID) -> None:
        template = await self.get_template(workspace_id=workspace_id, template_id=template_id)
        if template.is_preset:
            raise ValidationError("Presets cannot be deleted.")
        template.deleted_at = datetime.now(UTC)
        await self._session.flush()

    # -- applying a template (§57, P17-T05) ---------------------------------

    async def instantiate(
        self,
        *,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        product: Product,
        directives: BrandDirectives | None = None,
    ) -> list[InstantiatedShot]:
        """Turn a template into shots for one product (§57).

        This is the sentence §57 rests on:

            用户套模板后：根据 Product 动态实例化 Storyboard。

        *Dynamically*, against the product. The slot's `intent` and narration
        carry placeholders that are filled from the product's own name, brand
        and category, so the same template applied to two products produces two
        different storyboards. Copying the blueprint verbatim would make a
        template a stencil, and every video made from one identical.

        Note what is *not* done here: no prompt is compiled. §19 owns that, and
        a template that wrote prompts directly would route around it.
        """
        template = await self.get_template(workspace_id=workspace_id, template_id=template_id)
        blueprint = TemplateBlueprint(
            slots=[BlueprintSlot.model_validate(slot) for slot in template.storyboard_blueprint]
        )

        values = {
            "product": product.name,
            "brand": product.brand_name or product.name,
            "category": product.category,
        }
        rules = tuple(template.prompt_rules)
        if directives and directives.visual_guidelines:
            rules = (*rules, directives.visual_guidelines)

        shots = [
            InstantiatedShot(
                sequence_no=slot.sequence_no,
                shot_type=slot.shot_type,
                duration_seconds=slot.duration_seconds,
                description=_fill(slot.intent, values),
                camera=slot.camera,
                motion=slot.motion,
                lighting=slot.lighting,
                composition=slot.composition,
                voiceover_text=_fill(slot.narration_template, values),
                identity_lock=slot.identity_lock,
                extra_prompt_rules=rules,
            )
            for slot in sorted(blueprint.slots, key=lambda entry: entry.sequence_no)
        ]

        # Counted so the gallery can order by what people actually use. Not a
        # separate analytics write: it is one column on a row already loaded.
        template.usage_count += 1
        await self._session.flush()

        logger.info(
            "template_instantiated",
            extra={
                "template_id": str(template_id),
                "product_id": str(product.id),
                "shots": len(shots),
            },
        )
        return shots

    async def count_templates(self, *, workspace_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Template)
            .where(Template.workspace_id == workspace_id, Template.deleted_at.is_(None))
        )
        return int(result.scalar() or 0)


_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\{(\w+)\}")


def _fill(text: str, values: dict[str, str]) -> str:
    """Substitute `{product}`-style placeholders in one pass.

    One pass, for the same reason §108 requires it of the prompt registry: a
    sequential replace lets a substituted value introduce a placeholder that a
    later replacement then fills, which is an injection with extra steps. An
    unknown placeholder is left as written rather than raising — a template
    with a typo should produce a visibly odd line, not a failed project.
    """
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def _validate_colors(fields: dict[str, Any]) -> None:
    """Refuse a colour that is not `#RRGGBB` (§35).

    Strict because these strings end up inside an ffmpeg `force_style`
    argument. A hex triple has no metacharacters; "red; Outline=99" does.
    """
    for key in ("primary_color", "secondary_color", "subtitle_color"):
        value = fields.get(key)
        if value is not None and not _HEX.match(str(value)):
            raise ValidationError(
                "Colours must be written as #RRGGBB.",
                details={"field": key, "value": str(value)[:32]},
            )


def default_transition(template: Template | None) -> TransitionType:
    return template.transition_style if template else TransitionType.CUT


__all__ = [
    "MAX_SLOTS",
    "BlueprintSlot",
    "BrandDirectives",
    "BrandingService",
    "InstantiatedShot",
    "TemplateBlueprint",
    "default_transition",
]
