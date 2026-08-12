"""The Anthropic vision adapter, against a stubbed client (§20, P6-T05).

**This does not prove the adapter works against the live API.** Nobody has
supplied a key, so the one thing these tests cannot check is whether the
request shape is the one the vendor actually accepts. That gap is real and is
recorded in the module's own docstring rather than papered over.

What *is* covered is everything reachable without a key, which is most of the
adapter's risk: request construction, image downscaling, response parsing, the
refusal and truncation branches, and every error-mapping arm. Those are the
paths that would otherwise be discovered in production, at the worst moment.
"""

from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from PIL import Image

from backend_core.config import Settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.providers.anthropic_vision import (
    _MAX_IMAGE_EDGE,
    _SCHEMA,
    AnthropicVisionProvider,
)
from backend_core.providers.base import ProviderImage, VisionProvider
from backend_core.providers.schemas import ProductIntelligence

# --- fixtures ---------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    base: dict[str, Any] = {
        "jwt_secret": "x" * 32,
        "anthropic_api_key": "sk-ant-test",
        "use_mock_providers": False,
    }
    base.update(overrides)
    return Settings(**base)


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _image(width: int = 64, height: int = 64) -> ProviderImage:
    return ProviderImage(data=_jpeg(width, height), mime_type="image/jpeg", role="MAIN")


_VALID_PAYLOAD: dict[str, Any] = {
    "product_name": "静音空气净化器",
    "category": "家用电器",
    "brand": "",
    "colors": ["哑光白"],
    "materials": ["ABS 工程塑料"],
    "visible_text": [],
    "structural_features": ["圆角矩形机身"],
    "visual_features": ["细窄边框"],
    "possible_use_cases": ["客厅日常使用"],
    "possible_selling_points": ["外观简洁"],
    "uncertain_fields": ["brand"],
    "visual_dna": {
        "tone": ["干净"],
        "palette": ["哑光白"],
        "recommended_backgrounds": ["素色墙面"],
        "recommended_camera_styles": ["缓慢环绕"],
    },
}


class StubMessages:
    """Records the request and returns whatever the test asked for."""

    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class StubClient:
    def __init__(self, response: object | Exception) -> None:
        self.messages = StubMessages(response)


def _response(
    payload: dict[str, Any] | str | None = None,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5",
) -> SimpleNamespace:
    if payload is None:
        payload = _VALID_PAYLOAD
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=2100, output_tokens=380),
    )


def _provider(response: object | Exception, **overrides: object) -> AnthropicVisionProvider:
    return AnthropicVisionProvider(_settings(**overrides), client=StubClient(response))


def _status_error(status_code: int) -> anthropic.APIStatusError:
    """A vendor status error, built the way the SDK builds one."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request, json={"error": {"message": "no"}})
    return anthropic.APIStatusError("boom", response=response, body=None)


# --- request construction ---------------------------------------------------


class TestRequest:
    def test_it_sends_the_configured_model_and_effort(self) -> None:
        provider = _provider(_response(), anthropic_vision_model="claude-opus-5")
        provider.analyze_product([_image()], product_name="净化器")

        sent = provider._client.messages.calls[0]  # type: ignore[attr-defined]
        assert sent["model"] == "claude-opus-5"
        assert sent["output_config"]["effort"] == "medium"

    def test_it_constrains_the_response_to_the_schema(self) -> None:
        """§14 makes schema validation mandatory; asking is half of it."""
        provider = _provider(_response())
        provider.analyze_product([_image()])

        fmt = provider._client.messages.calls[0]["output_config"]["format"]  # type: ignore[attr-defined]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"] is _SCHEMA

    def test_the_schema_is_one_a_structured_output_call_accepts(self) -> None:
        """Every property required, no additional properties, recursively.

        The API rejects a schema that omits either, and the failure arrives as
        an opaque 400 at call time — which is exactly when nobody wants to be
        debugging a schema.
        """

        def check(node: dict[str, Any]) -> None:
            if node.get("type") != "object":
                return
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)

        check(_SCHEMA)

    def test_the_schema_matches_the_pydantic_model(self) -> None:
        """The hand-written contract and the model it validates into agree.

        Written by hand deliberately — a model whose fields all have defaults
        generates a schema the API rejects — so this is the test that keeps the
        two from drifting apart.
        """
        assert set(_SCHEMA["properties"]) == set(ProductIntelligence.model_fields)

    def test_images_come_before_the_instructions(self) -> None:
        """Images first, then the question about them — the documented order."""
        provider = _provider(_response())
        provider.analyze_product([_image(), _image()], product_name="净化器")

        content = provider._client.messages.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
        assert [block["type"] for block in content] == ["image", "image", "text"]

    def test_the_rendered_prompt_carries_the_product_context(self) -> None:
        provider = _provider(_response())
        provider.analyze_product([_image()], product_name="净化器", category="家用电器")

        content = provider._client.messages.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
        assert "净化器" in content[-1]["text"]
        assert "家用电器" in content[-1]["text"]

    def test_it_sends_no_more_images_than_configured(self) -> None:
        """Vision is billed per image; the cap is a spend control."""
        provider = _provider(_response(), vision_max_images=2)
        provider.analyze_product([_image(), _image(), _image(), _image()])

        content = provider._client.messages.calls[0]["messages"][0]["content"]  # type: ignore[attr-defined]
        assert sum(1 for block in content if block["type"] == "image") == 2

    def test_it_refuses_an_empty_image_list_without_calling_the_api(self) -> None:
        provider = _provider(_response())
        with pytest.raises(ProviderRejectedError):
            provider.analyze_product([])
        assert provider._client.messages.calls == []  # type: ignore[attr-defined]

    def test_image_data_is_base64_without_line_breaks(self) -> None:
        """MIME-wrapped base64 would be rejected by the API."""
        provider = _provider(_response())
        provider.analyze_product([_image()])

        block = provider._client.messages.calls[0]["messages"][0]["content"][0]  # type: ignore[attr-defined]
        assert "\n" not in block["source"]["data"]
        assert base64.standard_b64decode(block["source"]["data"])


class TestDownscaling:
    def test_a_small_image_is_passed_through_untouched(self) -> None:
        """Re-encoding a compliant image would cost quality for nothing."""
        image = _image(64, 64)
        provider = _provider(_response())
        provider.analyze_product([image])

        block = provider._client.messages.calls[0]["messages"][0]["content"][0]  # type: ignore[attr-defined]
        assert base64.standard_b64decode(block["source"]["data"]) == image.data

    def test_an_oversized_image_is_shrunk_to_the_useful_ceiling(self) -> None:
        """Beyond the model's resolution tier, extra pixels cost tokens only."""
        provider = _provider(_response())
        provider.analyze_product([_image(_MAX_IMAGE_EDGE * 2, _MAX_IMAGE_EDGE)])

        block = provider._client.messages.calls[0]["messages"][0]["content"][0]  # type: ignore[attr-defined]
        sent = Image.open(io.BytesIO(base64.standard_b64decode(block["source"]["data"])))
        assert max(sent.size) == _MAX_IMAGE_EDGE
        # Aspect ratio preserved: a squashed product photo is a wrong answer.
        assert sent.size == (_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE // 2)

    def test_undecodable_bytes_are_sent_as_they_are_rather_than_failing(self) -> None:
        """Validation already accepted the file (§12).

        A decode failure here is unexpected, and failing the whole analysis
        over a resize would turn a cosmetic problem into a user-visible one.
        """
        provider = _provider(_response())
        provider.analyze_product(
            [ProviderImage(data=b"not an image", mime_type="image/jpeg", role="MAIN")]
        )

        block = provider._client.messages.calls[0]["messages"][0]["content"][0]  # type: ignore[attr-defined]
        assert base64.standard_b64decode(block["source"]["data"]) == b"not an image"


# --- responses --------------------------------------------------------------


class TestResponse:
    def test_a_valid_response_becomes_a_validated_analysis(self) -> None:
        result = _provider(_response()).analyze_product([_image()])
        assert result.intelligence.product_name == "静音空气净化器"
        assert result.intelligence.colors == ["哑光白"]
        assert result.provider == "anthropic"

    def test_it_records_the_prompt_it_used(self) -> None:
        """§15 — traceable months later."""
        result = _provider(_response()).analyze_product([_image()])
        assert result.prompt_key == "product_analyze_v1"
        assert result.prompt_version >= 1

    def test_it_reports_the_model_the_provider_says_it_served(self) -> None:
        """Not the model we asked for: vendors route to newer snapshots (§20)."""
        result = _provider(_response(model="claude-opus-5-20260101")).analyze_product([_image()])
        assert result.usage.model == "claude-opus-5-20260101"
        assert result.usage.input_tokens == 2100
        assert result.usage.output_tokens == 380
        assert result.usage.latency_ms is not None

    def test_the_diagnostic_blob_holds_no_product_description(self) -> None:
        """§62 — `raw` reaches logs, and the analysis describes an unreleased
        product. Counts and identifiers only."""
        result = _provider(_response()).analyze_product([_image()])
        assert set(result.raw) == {"stop_reason", "image_count", "prompt_version"}
        assert "静音空气净化器" not in json.dumps(result.raw, ensure_ascii=False)

    def test_a_refusal_is_detected_before_the_content_is_read(self) -> None:
        """A safety refusal arrives as HTTP 200 with no content blocks.

        Reading `content[0]` first would raise `IndexError` on a perfectly
        well-formed response, which is how a refusal turns into a mystery
        crash. The order of the checks is the whole point of this test.
        """
        response = SimpleNamespace(
            content=[], stop_reason="refusal", model="claude-opus-5", usage=None
        )
        with pytest.raises(ProviderRejectedError):
            _provider(response).analyze_product([_image()])

    def test_a_truncated_response_is_not_a_provider_error(self) -> None:
        """The call succeeded and was billed; the output is just unusable.

        Classifying it as a transport failure would put it in the retry path,
        where an identical request would truncate identically.
        """
        with pytest.raises(ValueError) as caught:
            _provider(_response(stop_reason="max_tokens")).analyze_product([_image()])
        assert not isinstance(caught.value, ProviderUnavailableError)

    def test_output_that_does_not_match_the_schema_is_rejected(self) -> None:
        """The vendor guarantees the shape; §14 makes us check anyway."""
        payload = dict(_VALID_PAYLOAD, certified_efficiency="99.97%")
        with pytest.raises(ValueError):
            _provider(_response(payload)).analyze_product([_image()])

    def test_non_json_text_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _provider(_response("I'm afraid I can't do that")).analyze_product([_image()])

    def test_a_response_with_no_text_block_is_rejected(self) -> None:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="hmm")],
            stop_reason="end_turn",
            model="claude-opus-5",
            usage=None,
        )
        with pytest.raises(ValueError):
            _provider(response).analyze_product([_image()])


# --- error mapping (§20) ----------------------------------------------------


class TestErrorMapping:
    def test_rate_limiting_maps_to_our_rate_limited_error(self) -> None:
        error = anthropic.RateLimitError(
            "slow down", response=_status_error(429).response, body=None
        )
        with pytest.raises(ProviderRateLimitedError):
            _provider(error).analyze_product([_image()])

    def test_a_connection_failure_maps_to_unavailable(self) -> None:
        error = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        with pytest.raises(ProviderUnavailableError):
            _provider(error).analyze_product([_image()])

    def test_a_server_error_maps_to_unavailable_and_is_worth_retrying(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            _provider(_status_error(503)).analyze_product([_image()])

    def test_a_client_error_maps_to_rejected_and_is_not_worth_retrying(self) -> None:
        """Retrying an identical rejected request only spends money again."""
        with pytest.raises(ProviderRejectedError) as caught:
            _provider(_status_error(400)).analyze_product([_image()])
        assert caught.value.details["status_code"] == 400


class TestConstruction:
    def test_it_satisfies_the_vision_provider_protocol(self) -> None:
        assert isinstance(_provider(_response()), VisionProvider)

    def test_a_missing_key_refuses_clearly_instead_of_failing_at_call_time(self) -> None:
        with pytest.raises(ProviderUnavailableError) as caught:
            AnthropicVisionProvider(_settings(anthropic_api_key=""))
        assert "USE_MOCK_PROVIDERS" in str(caught.value)
