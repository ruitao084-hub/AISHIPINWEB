"""Choosing a provider, and giving up on one (§55, PHASE 19).

§55 lists what routing weighs:

    capability match / availability / cost / latency / failure rate /
    quality mode / user preference

Those are not equal, and the order they are applied in is the design:

1. **Capability is a filter, not a factor.** §140 is explicit that the router
   may not assume providers are alike. A provider that cannot make a 7-second
   9:16 clip is not an expensive choice — it is not a choice, and scoring it
   at all risks it winning on price.
2. **Availability is a filter too.** A provider whose breaker is open is out,
   however cheap it is.
3. **Everything else is a score**, and the weights are stated in one place
   below rather than smeared through conditionals.

**The circuit breaker (P19-T05) exists because retrying an outage is worse than
failing fast.** §24 already retries individual jobs with backoff; without a
breaker, fifty jobs each retry three times into a provider that is down, which
is 150 requests, several minutes of queue time, and 150 chances to be
rate-limited on the way back up. The breaker turns the fifty-first job into an
immediate fallback.

**Fallback (P19-T06) is per attempt, not per job.** A job that failed against
Runway is re-queued by §24 and routed again — and because the breaker has since
opened, the next attempt lands somewhere else. That is why fallback needs no
separate machinery: routing on every attempt *is* the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import AspectRatio, JobStatus, QualityMode
from backend_core.domain.models import GenerationJob, ProviderConfig
from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger
from backend_core.providers.capabilities import (
    ProviderHealth,
    VideoCapability,
    capability_for,
)

logger = get_logger(__name__)


class NoProviderAvailableError(AppError):
    """Nothing can serve this request right now (§55)."""

    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = 503
    retryable = True
    default_message = "No provider can generate that right now."


# --- circuit breaker (P19-T05) ---------------------------------------------

#: Consecutive failures before the breaker opens. Five rather than three: a
#: provider that failed three times in a row is often a bad prompt or a
#: transient blip, and opening on that would route perfectly good traffic away
#: from the better model.
FAILURE_THRESHOLD: Final[int] = 5

#: How long the breaker stays open. Doubles on each consecutive trip, capped —
#: a provider that keeps failing on recovery should be probed less often, and a
#: fixed window means a long outage is retried every minute for its duration.
BASE_OPEN_SECONDS: Final[int] = 120
MAX_OPEN_SECONDS: Final[int] = 1800

#: Recent attempts the health window considers. Short, because "is this
#: provider working *now*" is the question — a failure rate averaged over a
#: week keeps a recovered provider in the doghouse.
HEALTH_WINDOW_MINUTES: Final[int] = 30


# --- scoring weights (§55) -------------------------------------------------
#
# Stated here rather than inlined, because the trade-off between them is a
# product decision someone will want to revisit, and hunting it through a
# comparison function is how it ends up never being revisited.
#
# Failure rate outweighs cost by design: a cheap provider that fails half the
# time costs more than an expensive one that works, once retries are counted.
_WEIGHT_FAILURE: Final[float] = 100.0
_WEIGHT_COST: Final[float] = 10.0
_WEIGHT_LATENCY: Final[float] = 0.1
_WEIGHT_PRIORITY: Final[float] = 1.0


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """What has to be generated (§55)."""

    duration_seconds: float
    aspect_ratio: AspectRatio
    quality: QualityMode
    reference_image_count: int = 0
    #: §55's "user preference". Honoured when the provider can serve the
    #: request and its breaker is closed; ignored otherwise, because a
    #: preference that routed to a provider that cannot serve the request
    #: would be a preference for failure.
    preferred_provider: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Which provider won, and why.

    `reason` is recorded on the job. When someone asks why a video came from
    the expensive provider, the answer should be in the row rather than
    reconstructed from what the config happened to say at the time.
    """

    provider: str
    model: str
    score: float
    reason: str
    #: Providers that could have served it, best first. The fallback order for
    #: §24's next attempt, and the evidence for the decision.
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    config: ProviderConfig
    capability: VideoCapability
    health: ProviderHealth
    score: float


class ProviderRouter:
    """§55's router, plus the breaker that makes fallback happen."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    # -- routing (P19-T03) --------------------------------------------------

    async def route(self, request: RoutingRequest, *, kind: str = "video") -> RoutingDecision:
        """Pick a provider, or raise if none can serve this (§55).

        Raises `NoProviderAvailableError`, which §24 classifies as retryable —
        correctly, because "everything is down right now" usually stops being
        true, unlike "this prompt is against policy".
        """
        candidates = await self._candidates(request, kind=kind)

        if not candidates:
            configs = await self._configs(kind)
            logger.warning(
                "routing_no_candidates",
                extra={
                    "duration": request.duration_seconds,
                    "ratio": request.aspect_ratio.value,
                    "quality": request.quality.value,
                    "configured": [config.provider for config in configs],
                },
            )
            raise NoProviderAvailableError(
                "No provider can generate that right now.",
                details={
                    "duration_seconds": request.duration_seconds,
                    "aspect_ratio": request.aspect_ratio.value,
                    "quality": request.quality.value,
                },
            )

        ordered = sorted(candidates, key=lambda entry: entry.score)

        # §55's user preference, applied *after* filtering. A preference for a
        # provider that cannot serve the request is not honoured, because
        # honouring it would mean choosing a failure on purpose.
        chosen = ordered[0]
        reason = "best score"
        if request.preferred_provider:
            preferred = next(
                (entry for entry in ordered if entry.config.provider == request.preferred_provider),
                None,
            )
            if preferred is not None:
                chosen = preferred
                reason = "user preference"
            else:
                reason = "preference unavailable; best score"

        decision = RoutingDecision(
            provider=chosen.config.provider,
            model=chosen.config.model or chosen.capability.model,
            score=round(chosen.score, 3),
            reason=reason,
            alternatives=tuple(entry.config.provider for entry in ordered if entry is not chosen),
        )
        logger.info(
            "routing_decision",
            extra={
                "provider": decision.provider,
                "score": decision.score,
                "reason": decision.reason,
                "alternatives": list(decision.alternatives),
            },
        )
        return decision

    async def _candidates(self, request: RoutingRequest, *, kind: str) -> list[Candidate]:
        """Every provider that *can* serve this, scored."""
        now = datetime.now(UTC)
        candidates: list[Candidate] = []

        for config in await self._configs(kind):
            if not config.enabled:
                continue
            # The breaker (P19-T05). Open means out, regardless of price.
            if config.circuit_open_until is not None and config.circuit_open_until > now:
                continue

            capability = capability_for(config.provider)
            if capability is None:
                # Undeclared capability is not permission to try. §140's whole
                # point is that assuming uniformity breaks requests.
                logger.warning(
                    "routing_skipped_undeclared_capability",
                    extra={"provider": config.provider},
                )
                continue

            if not capability.can_serve(
                duration_seconds=request.duration_seconds,
                ratio=request.aspect_ratio,
                quality=request.quality,
                reference_image_count=request.reference_image_count,
            ):
                continue

            health = await self.health(config.provider)
            candidates.append(
                Candidate(
                    config=config,
                    capability=capability,
                    health=health,
                    score=_score(config, capability, health, request),
                )
            )

        return candidates

    async def _configs(self, kind: str) -> list[ProviderConfig]:
        result = await self._session.execute(
            select(ProviderConfig)
            .where(ProviderConfig.kind == kind)
            .order_by(ProviderConfig.priority)
        )
        return list(result.scalars().all())

    # -- health (P19-T04) ---------------------------------------------------

    async def health(self, provider: str) -> ProviderHealth:
        """Observed behaviour over the recent window.

        Derived from `generation_jobs` rather than from a counter, so a worker
        restart does not reset it and two API replicas cannot disagree. The
        window is short on purpose: the question is whether this provider is
        working *now*.
        """
        since = datetime.now(UTC) - timedelta(minutes=HEALTH_WINDOW_MINUTES)
        result = await self._session.execute(
            select(GenerationJob.status, GenerationJob.finished_at)
            .where(
                GenerationJob.provider == provider,
                GenerationJob.finished_at.is_not(None),
                GenerationJob.finished_at >= since,
            )
            .order_by(GenerationJob.finished_at.desc())
            .limit(200)
        )
        rows = list(result.all())
        if not rows:
            return ProviderHealth(provider=provider)

        failed_states = {JobStatus.FAILED, JobStatus.TIMEOUT}
        failures = sum(1 for status, _ in rows if status in failed_states)

        # Consecutive failures counted from the newest backwards — that is what
        # the breaker acts on, and a rate alone cannot distinguish "five
        # failures spread over an hour" from "five in a row just now".
        consecutive = 0
        for status, _ in rows:
            if status not in failed_states:
                break
            consecutive += 1

        return ProviderHealth(
            provider=provider,
            attempts=len(rows),
            failures=failures,
            consecutive_failures=consecutive,
        )

    async def health_report(self, *, kind: str = "video") -> list[ProviderHealth]:
        """Health for every configured provider, for §99's monitor."""
        return [await self.health(config.provider) for config in await self._configs(kind)]

    # -- circuit breaker (P19-T05) ------------------------------------------

    async def record_failure(self, provider: str, *, kind: str = "video") -> bool:
        """Note a failure and open the breaker if it has had enough.

        Returns whether the breaker is now open. Called from the job runner's
        failure path, which is the only place that knows an attempt finished
        badly.
        """
        health = await self.health(provider)
        if health.consecutive_failures < FAILURE_THRESHOLD:
            return False

        config = await self.get_config(provider, kind=kind)
        if config is None:
            return False
        now = datetime.now(UTC)
        if config.circuit_open_until is not None and config.circuit_open_until > now:
            return True

        # Exponential, capped. A provider that fails again immediately after
        # the breaker closes should be probed less often, not at the same rate.
        window = min(BASE_OPEN_SECONDS * (2**config.circuit_trip_count), MAX_OPEN_SECONDS)
        config.circuit_open_until = now + timedelta(seconds=window)
        config.circuit_trip_count += 1
        await self._session.flush()

        logger.warning(
            "circuit_opened",
            extra={
                "provider": provider,
                "consecutive_failures": health.consecutive_failures,
                "open_seconds": window,
                "trip_count": config.circuit_trip_count,
            },
        )
        return True

    async def record_success(self, provider: str, *, kind: str = "video") -> None:
        """Close the breaker after a success.

        Resets the trip count too. A provider that recovers and then works
        should not carry its previous outage's backoff into the next one — that
        would make the second incident's first probe half an hour away.
        """
        config = await self.get_config(provider, kind=kind)
        if config is None or config.circuit_open_until is None:
            return
        config.circuit_open_until = None
        config.circuit_trip_count = 0
        await self._session.flush()
        logger.info("circuit_closed", extra={"provider": provider})

    # -- configuration (P19-T01, P19-T07) -----------------------------------

    async def get_config(self, provider: str, *, kind: str = "video") -> ProviderConfig | None:
        result = await self._session.execute(
            select(ProviderConfig).where(
                ProviderConfig.provider == provider, ProviderConfig.kind == kind
            )
        )
        return result.scalar_one_or_none()

    async def list_configs(self, *, kind: str | None = None) -> list[ProviderConfig]:
        query = select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.priority)
        if kind is not None:
            query = query.where(ProviderConfig.kind == kind)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def set_enabled(
        self, provider: str, *, enabled: bool, kind: str = "video", notes: str | None = None
    ) -> ProviderConfig:
        """An operator's switch (P19-T07).

        Turning a provider *on* also clears its breaker. Someone re-enabling a
        provider means "try this again now", and leaving a breaker open would
        make the switch appear to do nothing.
        """
        config = await self.get_config(provider, kind=kind)
        if config is None:
            config = ProviderConfig(provider=provider, kind=kind, enabled=enabled)
            self._session.add(config)
        else:
            config.enabled = enabled
        if enabled:
            config.circuit_open_until = None
            config.circuit_trip_count = 0
        if notes is not None:
            config.notes = notes
        await self._session.flush()

        logger.info(
            "provider_config_changed",
            extra={"provider": provider, "kind": kind, "enabled": enabled},
        )
        return config

    async def ensure_defaults(self) -> list[ProviderConfig]:
        """Seed a config row for every declared capability (P19-T01).

        Idempotent, and run at first use rather than in a migration: the set of
        providers is code, and a migration that inserted them would drift from
        the capability table the moment one was added.
        """
        from backend_core.providers.capabilities import CAPABILITIES

        existing = {config.provider for config in await self._configs("video")}
        created: list[ProviderConfig] = []

        for name, capability in CAPABILITIES.items():
            if name in existing:
                continue
            config = ProviderConfig(
                provider=name,
                model=capability.model,
                kind="video",
                # The mock is enabled only when mocks are in use; a real
                # deployment that seeded an enabled mock could serve a customer
                # a placeholder video, which is worse than serving nothing.
                enabled=(name != "mock") or self._settings.use_mock_providers,
                priority=10 if name == "mock" else 100,
            )
            self._session.add(config)
            created.append(config)

        if created:
            await self._session.flush()
            logger.info(
                "provider_configs_seeded",
                extra={"providers": [config.provider for config in created]},
            )
        return created


def _score(
    config: ProviderConfig,
    capability: VideoCapability,
    health: ProviderHealth,
    request: RoutingRequest,
) -> float:
    """Lower is better (§55).

    A sum of weighted terms rather than a lexicographic sort, because the
    trade-offs are real: a slightly pricier provider that is currently working
    should beat a cheap one that is failing, and a strict ordering by cost
    could never express that.
    """
    cost_per_second = config.cost_per_second or capability.cost_per_second
    cost = cost_per_second * request.duration_seconds

    return (
        health.failure_rate * _WEIGHT_FAILURE
        + cost * _WEIGHT_COST
        + capability.typical_latency_seconds * _WEIGHT_LATENCY
        + config.priority * _WEIGHT_PRIORITY
    )


async def route_for_job(
    session: AsyncSession, job: GenerationJob, *, settings: Settings | None = None
) -> RoutingDecision:
    """Route from a job's stored input (§55, P19-T06).

    Called on *every* attempt, which is what makes fallback work without
    separate machinery: a job that failed against one provider is re-queued by
    §24, routed again, and — because the breaker has since opened — lands
    somewhere else.
    """
    payload = job.input_json
    return await ProviderRouter(session, settings=settings).route(
        RoutingRequest(
            duration_seconds=float(payload.get("duration_seconds", 5.0)),
            aspect_ratio=AspectRatio(str(payload.get("aspect_ratio", "9:16"))),
            quality=QualityMode(str(payload.get("quality_mode", "STANDARD"))),
            reference_image_count=int(payload.get("reference_image_count", 0)),
            preferred_provider=_preferred(payload.get("preferred_provider")),
        )
    )


def _preferred(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


__all__ = [
    "BASE_OPEN_SECONDS",
    "FAILURE_THRESHOLD",
    "MAX_OPEN_SECONDS",
    "Candidate",
    "NoProviderAvailableError",
    "ProviderRouter",
    "RoutingDecision",
    "RoutingRequest",
    "route_for_job",
]
