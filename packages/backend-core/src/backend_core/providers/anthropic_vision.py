"""Vision analysis via the Anthropic Messages API (P6-T05).

§20's division of labour, applied: this module maps our request onto the
vendor's, maps the vendor's errors onto :mod:`backend_core.errors`, enforces a
timeout, and reports cost metadata. It touches no business state — no product
status, no facts, no credits. That is what makes it swappable.

**This adapter has never been run against the live API.** It cannot be: it
needs a key nobody has supplied yet. What *is* tested is everything reachable
without one — request construction, image downscaling, response parsing, and
every error-mapping branch — against a stubbed client. The gap is recorded
rather than papered over, because "it compiles" is not "it works".
"""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Final

from PIL import Image

from backend_core.config import Settings, get_settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.prompts.registry import active_version, get_prompt
from backend_core.providers.base import (
    ProviderImage,
    ProviderUsage,
    VisionAnalysis,
)
from backend_core.providers.schemas import ProductIntelligence

logger = get_logger(__name__)

_PROMPT_KEY: Final[str] = "product_analyze_v1"

#: Long-edge ceiling in pixels. The model's high-resolution tier tops out here,
#: so a larger image costs more tokens and returns nothing extra. Our own
#: upload limit is 16384px (§12), which would be roughly six times the price
#: for the same answer.
_MAX_IMAGE_EDGE: Final[int] = 2576

#: Generous, because the response budget covers thinking *and* the JSON. A
#: truncated response is indistinguishable from a malformed one downstream, so
#: it is worth over-provisioning rather than debugging a half-written object.
_MAX_TOKENS: Final[int] = 16_000

#: The JSON contract, written out rather than generated from the Pydantic
#: model. Structured outputs need every property listed in `required` and
#: `additionalProperties: false` on every object; deriving that from a model
#: whose fields all have defaults produces a schema the API rejects. Written
#: by hand, the contract is visible and cannot drift silently — the round-trip
#: test asserts it still validates into `ProductIntelligence`.
_STRING_LIST: Final[dict[str, Any]] = {"type": "array", "items": {"type": "string"}}

_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_name": {"type": "string"},
        "category": {"type": "string"},
        "brand": {"type": "string"},
        "colors": _STRING_LIST,
        "materials": _STRING_LIST,
        "visible_text": _STRING_LIST,
        "structural_features": _STRING_LIST,
        "visual_features": _STRING_LIST,
        "possible_use_cases": _STRING_LIST,
        "possible_selling_points": _STRING_LIST,
        "uncertain_fields": _STRING_LIST,
        "visual_dna": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tone": _STRING_LIST,
                "palette": _STRING_LIST,
                "recommended_backgrounds": _STRING_LIST,
                "recommended_camera_styles": _STRING_LIST,
            },
            "required": [
                "tone",
                "palette",
                "recommended_backgrounds",
                "recommended_camera_styles",
            ],
        },
    },
    "required": [
        "product_name",
        "category",
        "brand",
        "colors",
        "materials",
        "visible_text",
        "structural_features",
        "visual_features",
        "possible_use_cases",
        "possible_selling_points",
        "uncertain_fields",
        "visual_dna",
    ],
}


class AnthropicVisionProvider:
    """Analyses product imagery with a Claude vision model."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client if client is not None else _build_client(self._settings)

    @property
    def name(self) -> str:
        return "anthropic"

    def analyze_product(
        self,
        images: list[ProviderImage],
        *,
        product_name: str | None = None,
        category: str | None = None,
        language: str = "zh-CN",
    ) -> VisionAnalysis:
        if not images:
            raise ProviderRejectedError("No images were supplied for analysis.")

        prompt = get_prompt(_PROMPT_KEY)
        instructions = prompt.render(
            language=language,
            product_name=product_name or "",
            category=category or "",
        )

        content: list[dict[str, Any]] = [
            _image_block(image) for image in images[: self._settings.vision_max_images]
        ]
        content.append({"type": "text", "text": instructions})

        started = time.monotonic()
        response = self._send(content)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # §20 error mapping, and the one check that must come first: a safety
        # classifier declines with HTTP 200, so reading `content[0]` before
        # looking at `stop_reason` raises an IndexError on a perfectly
        # well-formed response.
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise ProviderRejectedError(
                "The provider declined to analyse these images.",
                details={"stop_reason": "refusal"},
            )
        if stop_reason == "max_tokens":
            # The JSON is truncated. Deliberately *not* a provider error: the
            # call succeeded and was billed, and the caller must treat it as
            # unparseable output rather than a transport fault.
            raise ValueError("The provider's response was cut off before the JSON completed.")

        intelligence = _parse(response)

        return VisionAnalysis(
            intelligence=intelligence,
            provider=self.name,
            prompt_key=prompt.key,
            prompt_version=prompt.version,
            usage=_usage(response, elapsed_ms),
            # Only counts and identifiers. The full response is the model's
            # description of a customer's unreleased product — §62 keeps that
            # out of diagnostics that reach logs.
            raw={
                "stop_reason": stop_reason,
                "image_count": len(content) - 1,
                "prompt_version": active_version(_PROMPT_KEY),
            },
        )

    def _send(self, content: list[dict[str, Any]]) -> Any:
        """Make the call, translating vendor exceptions into ours (§20)."""
        import anthropic

        try:
            return self._client.messages.create(
                model=self._settings.anthropic_vision_model,
                max_tokens=_MAX_TOKENS,
                output_config={
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                    "effort": self._settings.anthropic_vision_effort,
                },
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitedError("The vision provider is rate limiting.") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError("Could not reach the vision provider.") from exc
        except anthropic.APIStatusError as exc:
            # 5xx is the provider's problem and worth retrying; 4xx is ours and
            # is not — retrying an identical rejected request only spends money.
            if exc.status_code >= 500:
                raise ProviderUnavailableError(
                    "The vision provider returned a server error."
                ) from exc
            raise ProviderRejectedError(
                "The vision provider rejected the request.",
                details={"status_code": exc.status_code},
            ) from exc


def _image_block(image: ProviderImage) -> dict[str, Any]:
    """Encode one image as a base64 content block, downscaled if oversized."""
    data, media_type = _downscale(image)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            # `standard_b64encode` emits no line breaks; the wrapped variant
            # would produce a payload the API rejects.
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


def _downscale(image: ProviderImage) -> tuple[bytes, str]:
    """Shrink an image to the model's useful ceiling, or pass it through.

    Returns the original bytes untouched when it is already small enough —
    re-encoding a compliant image would cost quality for nothing.
    """
    try:
        with Image.open(io.BytesIO(image.data)) as opened:
            if max(opened.size) <= _MAX_IMAGE_EDGE:
                return image.data, image.mime_type

            ratio = _MAX_IMAGE_EDGE / max(opened.size)
            target = (max(1, round(opened.width * ratio)), max(1, round(opened.height * ratio)))
            resized = opened.convert("RGB").resize(target, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            resized.save(buffer, format="JPEG", quality=88)
    except OSError:
        # Validation already accepted this file (§12), so a decode failure here
        # is unexpected. Sending the original is better than failing the whole
        # analysis over a resize.
        logger.warning("vision_image_downscale_failed", extra={"mime_type": image.mime_type})
        return image.data, image.mime_type

    return buffer.getvalue(), "image/jpeg"


def _parse(response: Any) -> ProductIntelligence:
    """Validate the model's JSON against §14's schema.

    `output_config.format` constrains the response, but this validates it
    anyway: the guarantee is the vendor's, the consequence of it slipping is
    ours, and §14 makes schema validation mandatory rather than optional.
    """
    text = next(
        (block.text for block in response.content if getattr(block, "type", None) == "text"),
        None,
    )
    if not text:
        raise ValueError("The provider returned no text content.")

    payload = json.loads(text)
    return ProductIntelligence.model_validate(payload)


def _usage(response: Any, elapsed_ms: int) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    return ProviderUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        # What the provider says it served, not what we asked for — the two
        # differ when a vendor routes to a newer snapshot (§20).
        model=getattr(response, "model", None),
        latency_ms=elapsed_ms,
    )


def _build_client(settings: Settings) -> Any:
    """Construct the vendor client, or refuse clearly."""
    import anthropic

    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ProviderUnavailableError(
            "ANTHROPIC_API_KEY is not configured. Set it, or run with USE_MOCK_PROVIDERS=true."
        )
    return anthropic.Anthropic(api_key=key, timeout=settings.vision_timeout_seconds)
