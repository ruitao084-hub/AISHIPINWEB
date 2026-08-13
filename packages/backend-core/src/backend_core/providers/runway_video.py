"""Runway video generation adapter (§20, §21, PHASE 10).

§21 sets the criteria for the first real video provider: an official API,
image-to-video support, commercial terms, an available account, clear docs.
Runway meets the first three and the fifth. It does **not** meet the fourth for
this project — no key has been supplied — so this adapter is written to the
credential boundary and stops there.

**Nothing here has ever run.** Not one request, not one poll. The request
shape, the polling loop and the error mapping are written from the vendor's
documented REST surface, and every one of them is a guess until a key exists to
check it against. This is a materially weaker position than the Anthropic
adapters, which at least have their request construction pinned by tests
against a stubbed client — there is no stub here because there is no verified
shape to stub.

What it is good for: the moment a key arrives, the shape of the work is
visible, the orchestrator already drives it through `VideoProvider`, and what
remains is correcting field names against a live response rather than designing
an integration from nothing.

**Do not enable this in production without exercising it first.** The
`ENABLE_REAL_VIDEO_PROVIDER` flag exists so that turning it on is a deliberate
act, and §170's mock path carries the whole product until then.
"""

from __future__ import annotations

from typing import Any, Final

from backend_core.config import Settings, get_settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.providers.base import ProviderUsage
from backend_core.providers.video import (
    ProviderJobState,
    VideoRequest,
    VideoStatus,
    VideoSubmission,
)

logger = get_logger(__name__)

_BASE_URL: Final[str] = "https://api.dev.runwayml.com/v1"

#: Runway pins its API by date header rather than URL path.
_API_VERSION: Final[str] = "2024-11-06"

#: Vendor status strings mapped onto our smaller vocabulary. Unknown values map
#: to RUNNING rather than FAILED: a status we do not recognise means the vendor
#: added one, and treating that as a failure would abandon work that is
#: probably fine. The stuck-job sweeper (§161) catches anything that never
#: resolves.
_STATE_MAP: Final[dict[str, ProviderJobState]] = {
    "PENDING": ProviderJobState.PENDING,
    "THROTTLED": ProviderJobState.PENDING,
    "RUNNING": ProviderJobState.RUNNING,
    "SUCCEEDED": ProviderJobState.SUCCEEDED,
    "FAILED": ProviderJobState.FAILED,
    "CANCELLED": ProviderJobState.CANCELED,
    "CANCELED": ProviderJobState.CANCELED,
}


class RunwayVideoProvider:
    """Image-to-video via Runway's REST API.

    Constructed with an injectable HTTP client so the request shape can be
    exercised against a stub once somebody knows what the real one looks like.
    """

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client if client is not None else _build_client(self._settings)

    @property
    def name(self) -> str:
        return "runway"

    def submit(self, request: VideoRequest) -> VideoSubmission:
        """Start a generation.

        Runway's image-to-video endpoint wants a starting frame, which is what
        §29's identity references are for — a shot with no reference cannot use
        this path at all, and saying so here is better than sending a request
        that will be refused.
        """
        if not request.reference_images:
            raise ProviderRejectedError(
                "Runway image-to-video needs at least one reference image. "
                "Attach a product image to this shot, or use a text-to-video provider."
            )

        import base64

        payload: dict[str, Any] = {
            "model": self._settings.runway_video_model,
            # Data URI rather than a URL: handing a third party a presigned URL
            # to our own bucket leaks a credential (§11).
            "promptImage": (
                "data:image/jpeg;base64,"
                + base64.standard_b64encode(request.reference_images[0]).decode("ascii")
            ),
            "promptText": request.prompt[: self._settings.runway_prompt_max_chars],
            "duration": int(request.duration_seconds),
            "ratio": _ratio(request.aspect_ratio),
            **request.options,
        }

        response = self._post("/image_to_video", payload)
        provider_job_id = str(response.get("id", ""))
        if not provider_job_id:
            raise ProviderUnavailableError("Runway accepted the request but returned no job id.")

        logger.info("runway_submitted", extra={"provider_job_id": provider_job_id})
        return VideoSubmission(
            provider_job_id=provider_job_id,
            state=ProviderJobState.PENDING,
            request_redacted={
                # §62: never the prompt text or the image bytes.
                "prompt_chars": len(request.prompt),
                "duration": int(request.duration_seconds),
                "ratio": _ratio(request.aspect_ratio),
                "reference_image_count": len(request.reference_images),
                "model": self._settings.runway_video_model,
            },
        )

    def poll(self, provider_job_id: str) -> VideoStatus:
        response = self._get(f"/tasks/{provider_job_id}")
        raw_state = str(response.get("status", "")).upper()
        state = _STATE_MAP.get(raw_state, ProviderJobState.RUNNING)

        if state is ProviderJobState.FAILED:
            return VideoStatus(
                state=state,
                error_code=str(response.get("failureCode") or "PROVIDER_REJECTED"),
                error_message=str(response.get("failure") or "The provider reported a failure."),
                raw={"status": raw_state},
            )

        output = response.get("output") or []
        result_url = str(output[0]) if output else None

        return VideoStatus(
            state=state,
            progress=int(float(response.get("progress") or 0) * 100),
            # Temporary and short-lived. §27 requires a worker to download and
            # re-host this before anything else may reference it.
            result_url=result_url,
            usage=ProviderUsage(model=self._settings.runway_video_model),
            raw={"status": raw_state},
        )

    def cancel(self, provider_job_id: str) -> None:
        """Best-effort stop.

        A 404 means the task already finished or never existed; either way the
        user's intent is satisfied and there is nothing for them to act on.
        """
        try:
            self._delete(f"/tasks/{provider_job_id}")
        except ProviderRejectedError:
            logger.info("runway_cancel_too_late", extra={"provider_job_id": provider_job_id})

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.runway_api_key.get_secret_value()}",
            "X-Runway-Version": _API_VERSION,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def _delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One place for §20's error mapping."""
        import httpx

        try:
            response = self._client.request(
                method, f"{_BASE_URL}{path}", headers=self._headers(), **kwargs
            )
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("Could not reach the video provider.") from exc

        if response.status_code == 429:
            raise ProviderRateLimitedError("The video provider is rate limiting.")
        if response.status_code >= 500:
            raise ProviderUnavailableError("The video provider returned a server error.")
        if response.status_code >= 400:
            # 4xx is our request's problem; retrying an identical one only
            # spends money to receive the same refusal (§24).
            raise ProviderRejectedError(
                "The video provider rejected the request.",
                details={"status_code": response.status_code},
            )

        if not response.content:
            return {}
        payload: dict[str, Any] = response.json()
        return payload


def _ratio(aspect_ratio: str) -> str:
    """Our frame vocabulary in the vendor's terms.

    Runway takes explicit pixel dimensions rather than a ratio string, so this
    is a lookup and not a reformat. Unknown ratios fall back to portrait, which
    is what most of this product's output is.
    """
    return {
        "9:16": "768:1280",
        "16:9": "1280:768",
        "1:1": "960:960",
        "4:5": "832:1040",
    }.get(aspect_ratio, "768:1280")


def _build_client(settings: Settings) -> Any:
    import httpx

    if not settings.runway_api_key.get_secret_value():
        raise ProviderUnavailableError(
            "RUNWAY_API_KEY is not configured. Set it, or run with USE_MOCK_PROVIDERS=true."
        )
    return httpx.Client(timeout=settings.video_request_timeout_seconds)


__all__ = ["RunwayVideoProvider"]
