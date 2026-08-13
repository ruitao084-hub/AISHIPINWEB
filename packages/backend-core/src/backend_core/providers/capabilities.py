"""What each provider can actually do (§140, §54, P19-T02).

§140 ends with the sentence this module exists to enforce:

    Router 不允许假设所有模型能力一样。

Providers differ in ways that break a request rather than degrade it. One
accepts a reference image and one ignores the field. One offers 5 and 10
seconds and nothing between. One will not do 9:16 at all. A router that
assumed uniformity would send a valid request to a provider that cannot serve
it and read the refusal as an outage.

So capability is *data*, declared per provider-model, and the router filters on
it before it considers cost or latency. A provider that cannot do the job is
not a slow choice — it is not a choice.

**Why a frozen dataclass rather than a database table.** §54 allows either.
Capabilities change when a vendor ships a model, not when an operator edits a
row, and putting them in code means a capability that stops being true fails a
type check rather than silently mismatching an enum. `ProviderConfig` — the
part operators *do* change, enable/disable and priority — is the database half.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend_core.domain.enums import AspectRatio, QualityMode


@dataclass(frozen=True, slots=True)
class VideoCapability:
    """§140's schema, as a value.

    Every field is a reason a request might not be servable. `durations` and
    `ratios` are the two that bite most often: a provider offering exactly
    `[5, 10]` cannot make a 7-second shot, and asking it to produces a rejected
    request that looks like a provider error.
    """

    provider: str
    model: str

    text_to_video: bool = True
    image_to_video: bool = False
    reference_images: bool = False
    max_reference_images: int = 0

    #: Exact durations the provider offers, in seconds. Empty means continuous
    #: within `max_duration_seconds` — which is rarer than vendors imply.
    durations: tuple[float, ...] = ()
    max_duration_seconds: float = 10.0

    ratios: tuple[AspectRatio, ...] = ()
    resolutions: tuple[str, ...] = ()

    audio: bool = False
    cancel: bool = True
    webhook: bool = False

    #: Credits per second, for §54's `cost_formula`. The router's cost term.
    cost_per_second: float = 1.0
    #: Typical seconds to finish a 5-second clip. Used to break ties, not to
    #: promise anything — it is an observation, not an SLA.
    typical_latency_seconds: float = 60.0

    #: Which quality tiers this model is a sensible answer for. A fast, cheap
    #: model is the wrong choice for PREMIUM even when it *can* serve the
    #: request, and a router that only checked capability would pick it.
    quality_modes: tuple[QualityMode, ...] = (
        QualityMode.FAST,
        QualityMode.STANDARD,
        QualityMode.HIGH,
        QualityMode.PREMIUM,
    )

    def supports_duration(self, seconds: float) -> bool:
        """Whether this provider can produce a clip of exactly `seconds`.

        Exact, not "close enough". A provider asked for 7 and given 5 returns a
        clip that no longer matches the shot it was timed against, and §33's
        timeline then drifts against the narration.
        """
        if seconds > self.max_duration_seconds:
            return False
        if not self.durations:
            return seconds > 0
        return any(abs(seconds - offered) < 0.01 for offered in self.durations)

    def supports_ratio(self, ratio: AspectRatio) -> bool:
        return not self.ratios or ratio in self.ratios

    def supports_quality(self, mode: QualityMode) -> bool:
        return mode in self.quality_modes

    def can_serve(
        self,
        *,
        duration_seconds: float,
        ratio: AspectRatio,
        quality: QualityMode,
        reference_image_count: int = 0,
    ) -> bool:
        """Whether this provider can serve a request at all (§140).

        The router calls this first. Everything after it — cost, latency,
        health — only orders the providers that pass.
        """
        if not self.supports_duration(duration_seconds):
            return False
        if not self.supports_ratio(ratio):
            return False
        if not self.supports_quality(quality):
            return False
        if reference_image_count > 0:
            if not self.reference_images:
                return False
            if reference_image_count > self.max_reference_images:
                return False
        return True

    def nearest_duration(self, seconds: float) -> float:
        """The closest duration this provider offers.

        For a caller that would rather adjust a shot than lose a provider.
        Returned as information — the decision to change a shot's length is the
        storyboard's, not the router's.
        """
        if not self.durations:
            return min(seconds, self.max_duration_seconds)
        return min(self.durations, key=lambda offered: abs(offered - seconds))


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Observed behaviour, as opposed to declared capability (P19-T04).

    Kept apart from `VideoCapability` because they answer different questions
    and change on different timescales. Capability is what a vendor promises;
    this is what happened last hour.
    """

    provider: str
    attempts: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    average_latency_seconds: float = 0.0

    @property
    def failure_rate(self) -> float:
        """0.0-1.0. Zero attempts reads as healthy, not as unknown.

        Deliberate: a provider nobody has called yet should be tried, and
        treating "no data" as "possibly bad" would leave a newly enabled
        provider permanently last in the ordering.
        """
        if self.attempts == 0:
            return 0.0
        return self.failures / self.attempts


#: The capabilities this platform knows about (§140, P19-T02).
#:
#: The mock is deliberately permissive — it exists so the whole pipeline runs
#: without keys (§21), and a mock that refused requests would make local
#: development harder without making it more realistic.
#:
#: Runway's entry reflects its actual constraints: 5 or 10 seconds and nothing
#: between, one reference image, no audio. That row is why the router exists.
CAPABILITIES: Final[dict[str, VideoCapability]] = {
    "mock": VideoCapability(
        provider="mock",
        model="mock-video-1",
        text_to_video=True,
        image_to_video=True,
        reference_images=True,
        max_reference_images=4,
        durations=(),
        max_duration_seconds=30.0,
        ratios=(),
        resolutions=("1080x1920", "1920x1080", "1080x1080"),
        audio=False,
        cancel=True,
        cost_per_second=0.0,
        typical_latency_seconds=2.0,
    ),
    "runway": VideoCapability(
        provider="runway",
        model="gen4_turbo",
        text_to_video=True,
        image_to_video=True,
        reference_images=True,
        max_reference_images=1,
        # Exactly two lengths. This single field is the clearest argument for
        # §140: a router that assumed continuous durations would send a
        # 7-second request and read the rejection as an outage.
        durations=(5.0, 10.0),
        max_duration_seconds=10.0,
        ratios=(AspectRatio.PORTRAIT_9_16, AspectRatio.LANDSCAPE_16_9),
        resolutions=("1280x720", "720x1280"),
        audio=False,
        cancel=True,
        webhook=True,
        cost_per_second=1.0,
        typical_latency_seconds=90.0,
        quality_modes=(QualityMode.STANDARD, QualityMode.HIGH, QualityMode.PREMIUM),
    ),
}


def capability_for(provider: str) -> VideoCapability | None:
    """What a provider can do, or `None` if we have never declared it.

    `None` rather than a permissive default: a provider whose capabilities are
    unknown must not be routed to, because the router's whole job is to avoid
    sending a request somewhere it cannot be served.
    """
    return CAPABILITIES.get(provider)


def capability_matrix() -> list[VideoCapability]:
    """Every declared capability, for §99's admin view."""
    return sorted(CAPABILITIES.values(), key=lambda entry: entry.provider)


__all__ = [
    "CAPABILITIES",
    "ProviderHealth",
    "VideoCapability",
    "capability_for",
    "capability_matrix",
]
