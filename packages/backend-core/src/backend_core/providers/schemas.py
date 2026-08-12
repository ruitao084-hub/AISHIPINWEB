"""Product Intelligence: the schema a vision provider must return (§14).

§14 makes schema validation mandatory and sets three rules:

- the AI must distinguish **observed** from **inferred**;
- anything it cannot confirm goes in `uncertain_fields`;
- an uncertain result is never automatically promoted to a fact.

The first rule is the one that shapes this module, and §14's own field names
already encode it. `colors`, `materials`, `visible_text` and the two feature
lists describe what is *visible in the photograph*. The two `possible_` lists
are the model's speculation about use and appeal — and §109 says
`possible_selling_points` may never be used as factual advertising.

So the observed/inferred boundary is not a per-item flag that a model has to
remember to set correctly; it is which field the value landed in. That makes
it structural, and :data:`OBSERVED_FIELDS` / :data:`INFERRED_FIELDS` are the
single place downstream code reads it from — the analysis-to-facts mapping in
PHASE 6 refuses to create a fact from anything in the inferred set.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class VisualDNA(BaseModel):
    """Aesthetic direction, not factual assertion (§14).

    Kept apart from facts on purpose: "warm, domestic, soft daylight" is a
    creative brief for PHASE 8's prompt compiler, and nobody needs to verify a
    colour palette the way they need to verify a filtration rating.
    """

    model_config = ConfigDict(extra="forbid")

    tone: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    recommended_backgrounds: list[str] = Field(default_factory=list)
    recommended_camera_styles: list[str] = Field(default_factory=list)


class ProductIntelligence(BaseModel):
    """What a vision provider returns about a product (§14).

    ``extra="forbid"`` is deliberate. A model that invents a field is a model
    whose output nobody checked, and silently dropping the extra key would hide
    exactly the drift that schema validation exists to catch.
    """

    model_config = ConfigDict(extra="forbid")

    # --- identity, as read from the images ---
    product_name: str = ""
    category: str = ""
    brand: str = ""

    # --- observed: visible in the photographs ---
    colors: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    #: Text legible on the product or packaging. Often the strongest evidence
    #: available, and still only `AI_INFERRED` until a person confirms it —
    #: OCR misreads, and a misread model number is a wrong product.
    visible_text: list[str] = Field(default_factory=list)
    structural_features: list[str] = Field(default_factory=list)
    visual_features: list[str] = Field(default_factory=list)

    # --- inferred: the model's speculation ---
    #: What the product might be used for. Never a fact.
    possible_use_cases: list[str] = Field(default_factory=list)
    #: What might be appealing about it. §109 forbids using these as factual
    #: advertising; they become `SUGGESTED` claims a person must approve.
    possible_selling_points: list[str] = Field(default_factory=list)

    #: Names of fields the model could not determine (§14). Surfaced to the
    #: reviewer as "the AI does not know this", which is far more useful than
    #: an empty list that could equally mean "there is nothing to say".
    uncertain_fields: list[str] = Field(default_factory=list)

    visual_dna: VisualDNA = Field(default_factory=VisualDNA)

    def observed_items(self) -> dict[str, list[str]]:
        """Every observation, keyed by field. Candidates for `AI_INFERRED` facts."""
        return {field: list(getattr(self, field)) for field in OBSERVED_FIELDS}

    def inferred_items(self) -> dict[str, list[str]]:
        """Every speculation, keyed by field. Never eligible to become a fact."""
        return {field: list(getattr(self, field)) for field in INFERRED_FIELDS}

    @property
    def is_empty(self) -> bool:
        """Whether the provider found nothing worth recording.

        A provider that returns a well-formed but empty analysis has failed at
        its job even though it did not raise, so the caller needs to be able to
        tell the difference.
        """
        return not any(self.observed_items().values()) and not self.product_name


#: Fields describing what is visible. These may become facts — at
#: `AI_INFERRED`, pending human confirmation (§13).
OBSERVED_FIELDS: Final[tuple[str, ...]] = (
    "colors",
    "materials",
    "visible_text",
    "structural_features",
    "visual_features",
)

#: Fields holding the model's speculation. §109 forbids these from being used
#: as factual advertising, so nothing here ever becomes a `ProductFact`.
INFERRED_FIELDS: Final[tuple[str, ...]] = (
    "possible_use_cases",
    "possible_selling_points",
)
