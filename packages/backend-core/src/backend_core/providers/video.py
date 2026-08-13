"""Video provider contract and the mock implementation (§20, §21, §172).

§21 sets the mock's requirements precisely: no outbound network, a fixed test
video, and support for success, failure, timeout **and cancellation**. The last
two are the ones usually skipped, and they are the ones the job orchestrator
gets wrong without them — a timeout that never arrives in testing is a timeout
nobody handled.

The shape here differs from the vision and LLM providers because the work does:
video generation takes minutes, so `submit` returns a handle and `poll` reports
on it. §22's worker loop is built on exactly that pair.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from backend_core.config import Settings, get_settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.providers.base import ProviderUsage

logger = get_logger(__name__)


class ProviderJobState(StrEnum):
    """What a provider says about one submitted job.

    Deliberately smaller than our own `JobStatus`: a provider knows whether it
    is working, done or failed, and nothing about queues, credits or retries.
    Mapping the two is the orchestrator's job, and keeping the vocabularies
    separate is what stops a vendor's status vocabulary leaking into ours.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass(frozen=True, slots=True)
class VideoRequest:
    """What to generate (§19, §29).

    `prompt` and `negative_prompt` arrive already compiled — §19 forbids a
    provider receiving a sentence a user typed, and by the time a request
    reaches here the compiler has run.

    `reference_images` are the identity frames §29 locks against, as bytes for
    the same reason vision images are: a presigned URL to our bucket is a
    credential.
    """

    prompt: str
    negative_prompt: str = ""
    duration_seconds: float = 5.0
    aspect_ratio: str = "9:16"
    reference_images: list[bytes] = field(default_factory=list)
    #: Vendor-specific knobs. Kept opaque so adding one is not a schema change,
    #: and so nothing in core business logic is tempted to read them.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoSubmission:
    """A provider has accepted the work."""

    provider_job_id: str
    state: ProviderJobState = ProviderJobState.PENDING
    #: Redacted echo of what was sent, for `provider_jobs` (§10.16, §62).
    request_redacted: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoStatus:
    """Where a submitted job has got to."""

    state: ProviderJobState
    progress: int = 0
    #: Temporary, provider-hosted. §27 forbids treating this as permanent: a
    #: worker downloads it and re-hosts before anything else may reference it.
    result_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    usage: ProviderUsage | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VideoProvider(Protocol):
    """Generates video clips (§20, §21).

    Three methods, because video is slow. `submit` hands over the work and
    returns immediately; `poll` reports; `cancel` stops. A synchronous
    `generate()` would force the caller to hold an HTTP request open for
    minutes, which §0.1 rule 13 forbids outright.
    """

    @property
    def name(self) -> str: ...

    def submit(self, request: VideoRequest) -> VideoSubmission:
        """Hand the work to the provider and return its handle."""
        ...

    def poll(self, provider_job_id: str) -> VideoStatus:
        """Ask how a submitted job is doing."""
        ...

    def cancel(self, provider_job_id: str) -> None:
        """Stop a submitted job.

        Best-effort by nature — a provider may already have finished. The
        orchestrator treats a failed cancel as "too late", not as an error,
        because there is nothing useful for a user to do about it.
        """
        ...


class MockVideoProvider:
    """A deterministic stand-in that never touches the network (§21, §172).

    Its state machine is time-based: a job reports `RUNNING` for a couple of
    seconds and then `SUCCEEDED`. That matters more than it looks — a mock that
    returns `SUCCEEDED` on the first poll lets a worker loop ship with a bug
    that only appears against a real provider, because the loop was never
    actually exercised.
    """

    #: Seconds of simulated work before a job completes.
    _WORK_SECONDS: float = 2.0

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Submission time per handle, so `poll` can age a job. Instance state
        # rather than global: two providers in one process must not share it.
        self._submitted: dict[str, float] = {}
        self._canceled: set[str] = set()

    @property
    def name(self) -> str:
        return "mock"

    def submit(self, request: VideoRequest) -> VideoSubmission:
        mode = self._settings.mock_video_mode
        if mode == "fail":
            # Refused at submission, which is a different failure from one that
            # appears mid-generation — and the orchestrator must handle both.
            raise ProviderRejectedError("Mock video provider rejected the request.")

        handle = _handle(request)
        self._submitted[handle] = time.monotonic()
        logger.info(
            "mock_video_submitted",
            extra={"provider_job_id": handle, "mode": mode, "duration": request.duration_seconds},
        )
        return VideoSubmission(
            provider_job_id=handle,
            state=ProviderJobState.PENDING,
            request_redacted={
                # Length rather than text: the prompt describes a customer's
                # unreleased product (§62).
                "prompt_chars": len(request.prompt),
                "negative_prompt_chars": len(request.negative_prompt),
                "duration_seconds": request.duration_seconds,
                "aspect_ratio": request.aspect_ratio,
                "reference_image_count": len(request.reference_images),
            },
        )

    def poll(self, provider_job_id: str) -> VideoStatus:
        if provider_job_id in self._canceled:
            return VideoStatus(state=ProviderJobState.CANCELED)

        started = self._submitted.get(provider_job_id)
        if started is None:
            # A handle this provider never issued. Permanent, not retryable —
            # polling harder will not make it exist.
            raise ProviderRejectedError(
                "Unknown provider job id.", details={"provider_job_id": provider_job_id}
            )

        mode = self._settings.mock_video_mode
        if mode == "timeout":
            # Never finishes. The orchestrator's stuck-job detection is what
            # has to notice, and this is the only way to exercise it.
            return VideoStatus(state=ProviderJobState.RUNNING, progress=50)

        elapsed = time.monotonic() - started
        wait = self._WORK_SECONDS * (10 if mode == "slow" else 1)

        if elapsed < wait:
            return VideoStatus(
                state=ProviderJobState.RUNNING,
                progress=min(95, int(elapsed / wait * 100)),
            )

        return VideoStatus(
            state=ProviderJobState.SUCCEEDED,
            progress=100,
            # A `file://` URL into the repo's own fixture. Deliberately not an
            # HTTP address: §21 says the mock must not touch the network, and
            # the ingestion path (§27) can read this exactly as it would a
            # provider's temporary URL.
            result_url=_fixture_url(),
            usage=ProviderUsage(model="mock-video-1", latency_ms=int(elapsed * 1000)),
            raw={"mock": True, "mode": mode},
        )

    def cancel(self, provider_job_id: str) -> None:
        self._canceled.add(provider_job_id)
        logger.info("mock_video_canceled", extra={"provider_job_id": provider_job_id})


def _handle(request: VideoRequest) -> str:
    """A stable id derived from the request, so a resubmission is recognisable."""
    digest = hashlib.sha256()
    digest.update(request.prompt.encode())
    digest.update(str(request.duration_seconds).encode())
    digest.update(str(time.monotonic_ns()).encode())
    return f"mock-{digest.hexdigest()[:24]}"


def _fixture_url() -> str:
    """The test clip shipped with the repo (§21's "fixed test video")."""
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "media" / "tiny.mp4"
    return fixture.as_uri()


def get_video_provider(settings: Settings | None = None) -> VideoProvider:
    """Build the configured video provider (§20, §170).

    The same three-switch rule as vision and LLM: mocks win, a real provider
    needs its own flag, and an unknown name is an error rather than a silent
    fallback.
    """
    resolved = settings or get_settings()

    if resolved.use_mock_providers or resolved.default_video_provider == "mock":
        return MockVideoProvider(resolved)

    if not resolved.enable_real_video_provider:
        raise ProviderUnavailableError(
            "A real video provider is configured but ENABLE_REAL_VIDEO_PROVIDER is off."
        )

    name = resolved.default_video_provider
    if name == "runway":
        from backend_core.providers.runway_video import RunwayVideoProvider

        provider: VideoProvider = RunwayVideoProvider(resolved)
        return provider

    raise ProviderUnavailableError(f"Unknown video provider: {name!r}")


def build_video_provider(name: str, settings: Settings | None = None) -> VideoProvider:
    """Build a provider *by name*, for §55's router (PHASE 19).

    Separate from `get_video_provider`, which answers "what is configured".
    This one answers "give me this specific one", because a router that has
    chosen Runway cannot express that through a setting.

    The three-switch rule (§20, §170) still applies to the real providers: a
    router choosing one when its feature flag is off is a misconfiguration, and
    silently substituting the mock would ship a customer a placeholder video.
    """
    resolved = settings or get_settings()

    if name == "mock":
        return MockVideoProvider(resolved)

    if not resolved.enable_real_video_provider:
        raise ProviderUnavailableError(
            f"The router chose {name!r} but ENABLE_REAL_VIDEO_PROVIDER is off."
        )

    if name == "runway":
        from backend_core.providers.runway_video import RunwayVideoProvider

        provider: VideoProvider = RunwayVideoProvider(resolved)
        return provider

    raise ProviderUnavailableError(f"Unknown video provider: {name!r}")


__all__ = [
    "MockVideoProvider",
    "ProviderJobState",
    "ProviderRateLimitedError",
    "VideoProvider",
    "VideoRequest",
    "VideoStatus",
    "VideoSubmission",
    "build_video_provider",
    "get_video_provider",
]
