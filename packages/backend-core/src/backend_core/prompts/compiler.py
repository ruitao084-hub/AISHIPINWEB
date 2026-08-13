"""Prompt compiler for video generation (§19, §29, P8-T06/T07/T08).

§19 opens with a prohibition, and it is the reason this module exists:

    严禁直接把用户一句自然语言送给视频模型。

Never hand a video model a sentence a user typed. Not because users write
badly, but because a video model reads a prompt as a whole and the parts that
must not be negotiable — the product's shape, its logo, the text on its
packaging — carry no more weight than the parts that should be. A free-text
prompt makes product identity a suggestion.

So a prompt is **assembled**, from §19's thirteen named blocks, in a fixed
order, from structured fields. What varies between shots is the content of the
blocks; what never varies is that the identity block is present and says the
same thing every time.

**§29's identity lock is the sharp end.** When a shot is locked, the
consistency rules §19 spells out are added verbatim — preserve shape,
structure, material, logo placement, packaging; add no components; do not alter
visible text. Verbatim rather than paraphrased per shot, because a paraphrase
is a chance to weaken one, and the whole point is that this text is not up for
negotiation.

Per-provider formatting is a separate concern (§19's "不同 Provider 可有独立
Prompt Formatter"): this module produces the *content*, and a formatter decides
whether a given vendor wants labelled blocks or one paragraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from backend_core.domain.enums import ShotType

# ---------------------------------------------------------------------------
# §19's consistency semantics, quoted rather than reworded.
#
# These seven lines are the taskbook's own text. They are a constant, not a
# template, because every rewording is an opportunity to soften one of them —
# and "preserve logo placement" softened into "keep the branding consistent" is
# how a generated product ends up with the logo on the wrong face.
# ---------------------------------------------------------------------------
IDENTITY_RULES: Final[tuple[str, ...]] = (
    "keep the exact uploaded product identity",
    "preserve shape",
    "preserve structure",
    "preserve material",
    "preserve logo placement",
    "preserve packaging appearance",
    "do not add components",
    "do not alter visible text",
)

#: What a video model must not produce. Two groups, and they are different in
#: kind: the first is generic model failure (extra limbs, warped geometry), the
#: second is product-specific and only matters because §29 says it does.
_GENERIC_NEGATIVES: Final[tuple[str, ...]] = (
    "blurry",
    "low resolution",
    "distorted geometry",
    "warped edges",
    "melting surfaces",
    "extra limbs",
    "deformed hands",
    "flickering",
    "jittery motion",
    "watermark",
    "signature",
    "compression artifacts",
    "oversaturated colors",
)

_IDENTITY_NEGATIVES: Final[tuple[str, ...]] = (
    "different product",
    "multiple products",
    "duplicated product",
    "altered logo",
    "misplaced logo",
    "invented text",
    "unreadable garbled text",
    "added buttons",
    "added components",
    "changed proportions",
    "changed material",
    "changed color",
    "different packaging",
)

#: Text overlays are the most reliable way a generated clip becomes unusable:
#: a model asked for a product shot will often add invented captions in a
#: language nobody can read, and no amount of post-processing removes them.
_TEXT_NEGATIVES: Final[tuple[str, ...]] = (
    "text overlay",
    "caption",
    "subtitle burned into frame",
    "logo of another brand",
)


@dataclass(frozen=True, slots=True)
class ShotPromptSpec:
    """Everything §19's blocks need, as structured fields.

    A dataclass rather than a dict so a missing block is a type error at the
    call site rather than a quietly empty section in a paid generation.
    """

    #: What the shot is of. The subject line, not the whole prompt.
    subject: str
    #: The product as it must appear — name, and the observed characteristics
    #: that came from verified facts. Never speculation.
    product_identity: str = ""
    environment: str = ""
    composition: str = ""
    lighting: str = ""
    camera: str = ""
    camera_motion: str = ""
    object_motion: str = ""
    material: str = ""
    style: str = ""
    brand: str = ""
    #: §29. When true the identity rules are added and the identity negatives
    #: switch on.
    identity_lock: bool = True
    #: Extra prohibitions for this specific shot, on top of the standard set.
    extra_negatives: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """A compiled prompt and its negative counterpart."""

    prompt: str
    negative_prompt: str
    #: The blocks in order, so a UI can show *why* the prompt says what it does
    #: rather than presenting an opaque wall of text a user cannot edit safely.
    blocks: tuple[tuple[str, str], ...]


def compile_shot_prompt(spec: ShotPromptSpec) -> CompiledPrompt:
    """Assemble §19's blocks into a prompt (P8-T06).

    Empty blocks are omitted rather than emitted as `LIGHTING:` with nothing
    after it — a video model reads a labelled empty section as a signal that
    lighting does not matter, which is the opposite of the intent.

    `CONSISTENCY RULES` is the one block that appears whether or not anything
    else does, when the lock is on.
    """
    blocks: list[tuple[str, str]] = [("SUBJECT", spec.subject.strip())]

    for label, value in (
        ("PRODUCT IDENTITY", spec.product_identity),
        ("ENVIRONMENT", spec.environment),
        ("COMPOSITION", spec.composition),
        ("LIGHTING", spec.lighting),
        ("CAMERA", spec.camera),
        ("CAMERA MOTION", spec.camera_motion),
        ("OBJECT MOTION", spec.object_motion),
        ("MATERIAL", spec.material),
        ("STYLE", spec.style),
        ("BRAND", spec.brand),
    ):
        cleaned = value.strip()
        if cleaned:
            blocks.append((label, cleaned))

    if spec.identity_lock:
        blocks.append(("CONSISTENCY RULES", "; ".join(IDENTITY_RULES)))

    negative = compile_negative_prompt(spec)
    blocks.append(("NEGATIVE RULES", negative))

    prompt = "\n".join(f"{label}: {value}" for label, value in blocks[:-1])
    return CompiledPrompt(prompt=prompt, negative_prompt=negative, blocks=tuple(blocks))


def compile_negative_prompt(spec: ShotPromptSpec) -> str:
    """Assemble the negative prompt (P8-T07).

    The identity negatives are conditional on the lock, and deliberately so.
    "different product" in a wide lifestyle shot that deliberately includes
    other objects fights the shot rather than protecting it; on a macro of the
    product it is the single most valuable line in the prompt.

    Deduplicated with order preserved: a repeated term reads to some models as
    emphasis and to others as noise, and neither is what was meant.
    """
    terms: list[str] = [*_GENERIC_NEGATIVES, *_TEXT_NEGATIVES]
    if spec.identity_lock:
        terms.extend(_IDENTITY_NEGATIVES)
    terms.extend(spec.extra_negatives)

    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        cleaned = term.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ", ".join(ordered)


def default_identity_lock(shot_type: ShotType) -> bool:
    """Whether §29's lock should default on for this kind of shot.

    On where the product fills the frame, off where it is one element of a
    scene. Not a safety compromise: the identity rules constrain *composition*
    as much as identity, and applying them to a wide room shot produces stiff
    catalogue footage while protecting a product that occupies forty pixels.
    A user can always turn it on.
    """
    from backend_core.domain.enums import PRODUCT_DOMINANT_SHOTS

    return shot_type in PRODUCT_DOMINANT_SHOTS


def product_identity_line(
    product_name: str,
    *,
    colors: list[str] | None = None,
    materials: list[str] | None = None,
    structural_features: list[str] | None = None,
) -> str:
    """Describe the product for the identity block, from verified facts only.

    The caller passes values that came from `VERIFIED` facts — §13's rule
    reaches this far, because a prompt asserting "brushed aluminium" about a
    plastic product produces a video that misrepresents it just as surely as a
    script would.
    """
    parts: list[str] = [product_name.strip()]
    for values in (colors, materials, structural_features):
        if values:
            parts.append(", ".join(value.strip() for value in values if value.strip()))
    return "; ".join(part for part in parts if part)


__all__ = [
    "IDENTITY_RULES",
    "CompiledPrompt",
    "ShotPromptSpec",
    "compile_negative_prompt",
    "compile_shot_prompt",
    "default_identity_lock",
    "product_identity_line",
]
