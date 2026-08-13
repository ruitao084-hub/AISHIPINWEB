"""Brand kit and template endpoints (§57, §58, PHASE 17).

Two resources with one thing in common: a shallow version of either would look
finished and do nothing. §58 forbids treating a brand kit as a logo uploader,
and §57 requires a template to be *instantiated against a product* rather than
copied — so this surface exposes tone, phrasing rules and endings alongside the
marks, and the template apply endpoint takes a product id.

One deliberate asymmetry: presets are readable from every workspace and
editable from none. `POST /templates/{id}/duplicate` is how a tenant starts
from one, which keeps the platform's library stable while letting anyone build
on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from aipvs_api.dependencies import SessionDep, require_permission
from aipvs_api.v1.schemas import ApiRequest
from backend_core.domain.enums import (
    HEX_COLOR_PATTERN,
    AspectRatio,
    BrandTone,
    LogoPosition,
    Permission,
    ProjectPurpose,
    TargetPlatform,
    TemplateCategory,
    TransitionType,
    VideoStyle,
)
from backend_core.domain.models import BrandKit, Template
from backend_core.services.branding import (
    BrandingService,
    InstantiatedShot,
    TemplateBlueprint,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["branding"])

#: `#RRGGBB` only. Enforced at the boundary as well as in the service, because
#: these strings end up inside an ffmpeg `force_style` argument (§35) and the
#: OpenAPI schema is what tells a client the rule before it sends one.
HexColor = Annotated[str, Field(pattern=HEX_COLOR_PATTERN)]


# --- brand kits (§58) ------------------------------------------------------


class BrandKitRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool = False

    logo_asset_id: uuid.UUID | None = None
    logo_position: LogoPosition = LogoPosition.BOTTOM_RIGHT

    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    subtitle_color: HexColor | None = None
    font_family: str | None = Field(default=None, max_length=120)

    tone: BrandTone = BrandTone.PROFESSIONAL
    required_phrases: list[str] = Field(default_factory=list, max_length=20)
    banned_phrases: list[str] = Field(default_factory=list, max_length=100)

    ending_line: str | None = Field(default=None, max_length=500)
    ending_cta: str | None = Field(default=None, max_length=200)
    visual_guidelines: str | None = Field(default=None, max_length=2000)


class UpdateBrandKitRequest(ApiRequest):
    """Every field optional. Absent means unchanged, not cleared."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool | None = None
    logo_asset_id: uuid.UUID | None = None
    logo_position: LogoPosition | None = None
    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    subtitle_color: HexColor | None = None
    font_family: str | None = Field(default=None, max_length=120)
    tone: BrandTone | None = None
    required_phrases: list[str] | None = Field(default=None, max_length=20)
    banned_phrases: list[str] | None = Field(default=None, max_length=100)
    ending_line: str | None = Field(default=None, max_length=500)
    ending_cta: str | None = Field(default=None, max_length=200)
    visual_guidelines: str | None = Field(default=None, max_length=2000)


class BrandKitResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    logo_asset_id: uuid.UUID | None
    logo_position: LogoPosition
    primary_color: str | None
    secondary_color: str | None
    subtitle_color: str | None
    font_family: str | None
    tone: BrandTone
    required_phrases: list[str]
    banned_phrases: list[str]
    ending_line: str | None
    ending_cta: str | None
    visual_guidelines: str | None
    created_at: datetime

    @classmethod
    def of(cls, kit: BrandKit) -> BrandKitResponse:
        return cls(
            id=kit.id,
            name=kit.name,
            description=kit.description,
            is_default=kit.is_default,
            logo_asset_id=kit.logo_asset_id,
            logo_position=kit.logo_position,
            primary_color=kit.primary_color,
            secondary_color=kit.secondary_color,
            subtitle_color=kit.subtitle_color,
            font_family=kit.font_family,
            tone=kit.tone,
            required_phrases=list(kit.required_phrases),
            banned_phrases=list(kit.banned_phrases),
            ending_line=kit.ending_line,
            ending_cta=kit.ending_cta,
            visual_guidelines=kit.visual_guidelines,
            created_at=kit.created_at,
        )


@router.post(
    "/brand-kits",
    response_model=BrandKitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a brand kit",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def create_brand_kit(
    workspace_id: uuid.UUID, payload: BrandKitRequest, session: SessionDep
) -> BrandKitResponse:
    kit = await BrandingService(session).create_brand_kit(
        workspace_id=workspace_id, **payload.model_dump()
    )
    return BrandKitResponse.of(kit)


@router.get(
    "/brand-kits",
    response_model=list[BrandKitResponse],
    summary="List brand kits",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def list_brand_kits(workspace_id: uuid.UUID, session: SessionDep) -> list[BrandKitResponse]:
    kits = await BrandingService(session).list_brand_kits(workspace_id=workspace_id)
    return [BrandKitResponse.of(kit) for kit in kits]


@router.get(
    "/brand-kits/{brand_kit_id}",
    response_model=BrandKitResponse,
    summary="Get a brand kit",
    dependencies=[require_permission(Permission.PRODUCT_READ)],
)
async def get_brand_kit(
    workspace_id: uuid.UUID, brand_kit_id: uuid.UUID, session: SessionDep
) -> BrandKitResponse:
    kit = await BrandingService(session).get_brand_kit(
        workspace_id=workspace_id, brand_kit_id=brand_kit_id
    )
    return BrandKitResponse.of(kit)


@router.patch(
    "/brand-kits/{brand_kit_id}",
    response_model=BrandKitResponse,
    summary="Update a brand kit",
    dependencies=[require_permission(Permission.PRODUCT_WRITE)],
)
async def update_brand_kit(
    workspace_id: uuid.UUID,
    brand_kit_id: uuid.UUID,
    payload: UpdateBrandKitRequest,
    session: SessionDep,
) -> BrandKitResponse:
    kit = await BrandingService(session).update_brand_kit(
        workspace_id=workspace_id,
        brand_kit_id=brand_kit_id,
        **payload.model_dump(exclude_unset=True),
    )
    return BrandKitResponse.of(kit)


@router.delete(
    "/brand-kits/{brand_kit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a brand kit",
    dependencies=[require_permission(Permission.PRODUCT_DELETE)],
)
async def delete_brand_kit(
    workspace_id: uuid.UUID, brand_kit_id: uuid.UUID, session: SessionDep
) -> None:
    """Soft-delete. Projects made under this kit keep their reference, because
    "which brand was this video made under" is asked about shipped videos."""
    await BrandingService(session).delete_brand_kit(
        workspace_id=workspace_id, brand_kit_id=brand_kit_id
    )


# --- templates (§57) -------------------------------------------------------


class TemplateRequest(ApiRequest):
    name: str = Field(min_length=1, max_length=120)
    category: TemplateCategory
    description: str | None = Field(default=None, max_length=2000)
    aspect_ratio: AspectRatio
    duration_seconds: int = Field(ge=5, le=600)
    style: VideoStyle
    purpose: ProjectPurpose
    target_platform: TargetPlatform
    blueprint: TemplateBlueprint
    prompt_rules: list[str] = Field(default_factory=list, max_length=20)
    subtitle_style: dict[str, Any] = Field(default_factory=dict)
    transition_style: TransitionType = TransitionType.CUT
    music_tags: list[str] = Field(default_factory=list, max_length=10)
    ending_style: str | None = Field(default=None, max_length=1000)


class UpdateTemplateRequest(ApiRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    category: TemplateCategory | None = None
    blueprint: TemplateBlueprint | None = None
    prompt_rules: list[str] | None = Field(default=None, max_length=20)
    subtitle_style: dict[str, Any] | None = None
    transition_style: TransitionType | None = None
    music_tags: list[str] | None = Field(default=None, max_length=10)
    ending_style: str | None = Field(default=None, max_length=1000)


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: TemplateCategory
    description: str | None
    is_preset: bool = Field(description="Platform-provided. Readable everywhere, editable nowhere.")
    preview_asset_id: uuid.UUID | None
    aspect_ratio: AspectRatio
    duration_seconds: int
    style: VideoStyle
    purpose: ProjectPurpose
    target_platform: TargetPlatform
    storyboard_blueprint: list[dict[str, Any]]
    prompt_rules: list[str]
    subtitle_style: dict[str, Any]
    transition_style: TransitionType
    music_tags: list[str]
    ending_style: str | None
    usage_count: int
    created_at: datetime

    @classmethod
    def of(cls, template: Template) -> TemplateResponse:
        return cls(
            id=template.id,
            name=template.name,
            category=template.category,
            description=template.description,
            is_preset=template.is_preset,
            preview_asset_id=template.preview_asset_id,
            aspect_ratio=template.aspect_ratio,
            duration_seconds=template.duration_seconds,
            style=template.style,
            purpose=template.purpose,
            target_platform=template.target_platform,
            storyboard_blueprint=list(template.storyboard_blueprint),
            prompt_rules=list(template.prompt_rules),
            subtitle_style=dict(template.subtitle_style),
            transition_style=template.transition_style,
            music_tags=list(template.music_tags),
            ending_style=template.ending_style,
            usage_count=template.usage_count,
            created_at=template.created_at,
        )


class ApplyTemplateRequest(ApiRequest):
    product_id: uuid.UUID = Field(
        description="The product to instantiate against (§57). Required: a "
        "template applied without one would be the same video every time."
    )


class InstantiatedShotResponse(BaseModel):
    sequence_no: int
    shot_type: str
    duration_seconds: float
    description: str
    camera: str
    motion: str
    lighting: str
    composition: str
    voiceover_text: str
    identity_lock: bool

    @classmethod
    def of(cls, shot: InstantiatedShot) -> InstantiatedShotResponse:
        return cls(
            sequence_no=shot.sequence_no,
            shot_type=shot.shot_type.value,
            duration_seconds=shot.duration_seconds,
            description=shot.description,
            camera=shot.camera,
            motion=shot.motion,
            lighting=shot.lighting,
            composition=shot.composition,
            voiceover_text=shot.voiceover_text,
            identity_lock=shot.identity_lock,
        )


@router.post(
    "/templates",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a template",
    dependencies=[require_permission(Permission.TEMPLATE_WRITE)],
)
async def create_template(
    workspace_id: uuid.UUID, payload: TemplateRequest, session: SessionDep
) -> TemplateResponse:
    fields = payload.model_dump(exclude={"blueprint"})
    template = await BrandingService(session).create_template(
        workspace_id=workspace_id,
        blueprint=payload.blueprint,
        **fields,
    )
    return TemplateResponse.of(template)


@router.get(
    "/templates",
    response_model=list[TemplateResponse],
    summary="Template gallery",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def list_templates(
    workspace_id: uuid.UUID,
    session: SessionDep,
    category: TemplateCategory | None = None,
    include_presets: bool = True,
) -> list[TemplateResponse]:
    """§57's gallery (P17-T06). Presets first, then by how often each is used."""
    templates = await BrandingService(session).list_templates(
        workspace_id=workspace_id, category=category, include_presets=include_presets
    )
    return [TemplateResponse.of(template) for template in templates]


@router.get(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    summary="Get a template",
    dependencies=[require_permission(Permission.PROJECT_READ)],
)
async def get_template(
    workspace_id: uuid.UUID, template_id: uuid.UUID, session: SessionDep
) -> TemplateResponse:
    template = await BrandingService(session).get_template(
        workspace_id=workspace_id, template_id=template_id
    )
    return TemplateResponse.of(template)


@router.patch(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    summary="Update a template",
    dependencies=[require_permission(Permission.TEMPLATE_WRITE)],
)
async def update_template(
    workspace_id: uuid.UUID,
    template_id: uuid.UUID,
    payload: UpdateTemplateRequest,
    session: SessionDep,
) -> TemplateResponse:
    fields = payload.model_dump(exclude={"blueprint"}, exclude_unset=True)
    template = await BrandingService(session).update_template(
        workspace_id=workspace_id,
        template_id=template_id,
        blueprint=payload.blueprint,
        **fields,
    )
    return TemplateResponse.of(template)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a template",
    dependencies=[require_permission(Permission.TEMPLATE_WRITE)],
)
async def delete_template(
    workspace_id: uuid.UUID, template_id: uuid.UUID, session: SessionDep
) -> None:
    await BrandingService(session).delete_template(
        workspace_id=workspace_id, template_id=template_id
    )


class DuplicateTemplateRequest(ApiRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)


@router.post(
    "/templates/{template_id}/duplicate",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Copy a template into this workspace",
    dependencies=[require_permission(Permission.TEMPLATE_WRITE)],
)
async def duplicate_template(
    workspace_id: uuid.UUID,
    template_id: uuid.UUID,
    payload: DuplicateTemplateRequest,
    session: SessionDep,
) -> TemplateResponse:
    """The only way to build on a preset, which is read-only everywhere."""
    template = await BrandingService(session).duplicate_template(
        workspace_id=workspace_id, template_id=template_id, name=payload.name
    )
    return TemplateResponse.of(template)


@router.post(
    "/templates/{template_id}/apply",
    response_model=list[InstantiatedShotResponse],
    summary="Instantiate a template against a product",
    dependencies=[require_permission(Permission.PROJECT_WRITE)],
)
async def apply_template(
    workspace_id: uuid.UUID,
    template_id: uuid.UUID,
    payload: ApplyTemplateRequest,
    session: SessionDep,
) -> list[InstantiatedShotResponse]:
    """§57's apply (P17-T05).

    Returns the shots a storyboard *would* contain, without creating one. A
    preview rather than a commit: choosing a template is a decision someone
    should be able to reverse by looking at the result, and creating a
    storyboard version per template tried would litter the project.
    """
    from backend_core.services.products import ProductService

    branding = BrandingService(session)
    product = await ProductService(session).get(
        workspace_id=workspace_id, product_id=payload.product_id
    )
    shots = await branding.instantiate(
        workspace_id=workspace_id, template_id=template_id, product=product
    )
    return [InstantiatedShotResponse.of(shot) for shot in shots]


__all__: list[str] = ["router"]
