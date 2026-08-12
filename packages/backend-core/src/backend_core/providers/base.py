"""The AI provider boundary (taskbook §20, §0.1 rule 6, §12 of the ground rules).

Every external model capability goes through an adapter. That is not a
stylistic preference: §0.1 forbids hardcoding Sora, Veo, Runway or any single
model into core business logic, because the model that is best today is not the
model that will be best in six months, and a provider name spread through the
codebase is a rewrite rather than a config change.

§20 fixes the division of labour precisely.

**A provider may:** map parameters, call its API, map status, map errors,
enforce a timeout, retry its own transport failures, return result addresses,
and report cost metadata.

**A provider may not:** change project or product status, deduct credits, write
storyboards, or render video. Those are business decisions, and a provider that
makes them cannot be swapped out — which defeats the abstraction. The rule is
enforceable by reading: nothing in `providers/` imports a repository or a
service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.providers.creative_schemas import (
    CreativePlanDraft,
    CreativePlanSet,
    ScriptDocument,
)
from backend_core.providers.schemas import ProductIntelligence


@dataclass(frozen=True, slots=True)
class ProviderImage:
    """One image handed to a vision provider.

    Bytes plus a MIME type rather than a URL: a provider must not be handed a
    presigned URL to *our* storage and left to fetch it, because that leaks a
    credential to a third party and makes the call depend on our bucket being
    reachable from their network. The worker fetches, the provider receives.
    """

    data: bytes
    mime_type: str
    #: Optional label for the reviewer, e.g. the product asset role.
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Cost metadata §20 requires a provider to report.

    Recorded per call so PHASE 18's credit system has real numbers to bill
    against rather than an estimate invented later.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Provider's own model identifier, as it reported it — not what we asked
    #: for. They differ when a provider silently routes to a newer snapshot.
    model: str | None = None
    #: Wall-clock duration of the call.
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class VisionAnalysis:
    """A completed product analysis, plus everything needed to audit it."""

    intelligence: ProductIntelligence
    provider: str
    prompt_key: str
    prompt_version: int
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    #: Redacted provider response detail, for diagnostics (§10.16).
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VisionProvider(Protocol):
    """Analyses product imagery into :class:`ProductIntelligence` (§14).

    Synchronous by design at this layer. Vision analysis is a seconds-scale
    call, unlike video generation (§22's job orchestrator), so the adapter
    stays simple and the *caller* decides whether to run it in a worker.

    Implementations raise the shared provider errors so callers branch on a
    stable taxonomy rather than on whichever exception a vendor SDK happens to
    define:

    * :class:`ProviderUnavailableError` — transport failure or 5xx; retryable.
    * :class:`ProviderRateLimitedError` — 429; retryable after a delay.
    * :class:`ProviderRejectedError` — the request was refused (safety filter,
      malformed input, unsupported image); **not** retryable, because retrying
      an identical rejected request just spends money.
    """

    @property
    def name(self) -> str:
        """Stable identifier recorded against every analysis."""
        ...

    def analyze_product(
        self,
        images: list[ProviderImage],
        *,
        product_name: str | None = None,
        category: str | None = None,
        language: str = "zh-CN",
    ) -> VisionAnalysis:
        """Describe a product from its photographs.

        ``product_name`` and ``category`` are hints the user already supplied,
        not answers — a provider that simply echoes them back has told us
        nothing, and the schema's `uncertain_fields` exists so it can say it
        does not know instead.
        """
        ...


@dataclass(frozen=True, slots=True)
class CreativeGeneration:
    """Three creative directions, plus everything needed to audit them (§16)."""

    plans: CreativePlanSet
    provider: str
    prompt_key: str
    prompt_version: int
    usage: ProviderUsage = field(default_factory=ProviderUsage)


@dataclass(frozen=True, slots=True)
class ScriptGeneration:
    """One generated script, plus its provenance (§17)."""

    document: ScriptDocument
    provider: str
    prompt_key: str
    prompt_version: int
    usage: ProviderUsage = field(default_factory=ProviderUsage)


@dataclass(frozen=True, slots=True)
class CreativeBrief:
    """Everything §16 lists as an input to the creative engine.

    A single object rather than eleven parameters, because the same brief goes
    to both engines and the script prompt must not silently disagree with the
    creative prompt about, say, the target duration.

    `verified_claims` is the field that carries P7-T09. It holds only claims a
    person approved — the caller is responsible for that, and does it by
    calling the Truth Layer's `get_verified_claims` rather than filtering a
    wider list here. A provider cannot check the difference and must not have
    to.
    """

    product_name: str
    category: str
    #: Confirmed product facts, rendered as short strings. `VERIFIED` only.
    verified_facts: list[str] = field(default_factory=list)
    #: Approved marketing claims. `VERIFIED` only (§109).
    verified_claims: list[str] = field(default_factory=list)
    #: Aesthetic direction from PHASE 6's analysis. Creative, not factual.
    visual_dna: dict[str, Any] = field(default_factory=dict)
    brand_notes: str = ""
    purpose: str = "SOCIAL_AD"
    target_platform: str = "DOUYIN"
    target_audience: str = ""
    language: str = "zh-CN"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30
    style: str = "CLEAN_MINIMAL"


@runtime_checkable
class LLMProvider(Protocol):
    """Generates creative plans and scripts from a brief (§16, §17, §20).

    Split from :class:`VisionProvider` rather than merged into one "AI
    provider": the two have different inputs, different failure modes and, in
    practice, different vendors. A single fat Protocol would force every
    implementation to stub the half it does not do.

    The same error taxonomy applies. Note especially that output failing schema
    validation is **not** a provider error — the call succeeded and was billed,
    and §107 wants it retried as a *parse* failure rather than a transport one.
    """

    @property
    def name(self) -> str:
        """Stable identifier recorded against every generation."""
        ...

    def generate_creative_plans(self, brief: CreativeBrief) -> CreativeGeneration:
        """Propose three distinct directions for the video (§16)."""
        ...

    def generate_script(
        self, brief: CreativeBrief, plan: CreativePlanDraft, *, character_budget: int
    ) -> ScriptGeneration:
        """Write a script following the chosen plan (§17).

        ``character_budget`` is the spoken-character allowance derived from the
        project's duration. Passed in rather than computed here because the
        budget is a product decision and a provider must not be able to change
        how long the finished video is.
        """
        ...


__all__ = [
    "CreativeBrief",
    "CreativeGeneration",
    "CreativePlanDraft",
    "CreativePlanSet",
    "LLMProvider",
    "ProductIntelligence",
    "ProviderImage",
    "ProviderRateLimitedError",
    "ProviderRejectedError",
    "ProviderUnavailableError",
    "ProviderUsage",
    "ScriptDocument",
    "ScriptGeneration",
    "VisionAnalysis",
    "VisionProvider",
]
