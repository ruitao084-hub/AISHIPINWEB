"""Creative and script generation via the Anthropic Messages API (§16, §17, §20).

The same division of labour as the vision adapter: map the request, map the
errors, enforce a timeout, report cost. No business state.

One thing this adapter does that the vision one does not: **it retries a parse
failure** (§107). Structured outputs constrain the response, but the retry
exists because §107 asks for it and because the failure mode it covers is real
— a model that returns two creative plans instead of three has produced a
well-formed JSON object that our schema rejects, and re-prompting is the fix.
A transport retry would not be: the call succeeded.

**This adapter has never been run against the live API**, for the same reason
as its vision sibling — no key has been supplied. Request construction, both
schemas, the parse-retry loop and every error-mapping branch are tested against
a stubbed client. Whether the request shape is one the vendor accepts is not
tested and cannot be until a key exists.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final, TypeVar

from pydantic import BaseModel

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import (
    MAX_SHOT_SECONDS,
    MIN_SHOT_SECONDS,
    SCRIPT_SECTIONS,
    ShotType,
    TransitionType,
)
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.prompts.registry import get_prompt
from backend_core.providers.base import (
    CreativeBrief,
    CreativeGeneration,
    ProviderUsage,
    ScriptGeneration,
    StoryboardGeneration,
)
from backend_core.providers.creative_schemas import (
    CreativePlanDraft,
    CreativePlanSet,
    ScriptDocument,
)
from backend_core.providers.storyboard_schemas import StoryboardDraft

logger = get_logger(__name__)

#: Whatever schema this call expects back. Generic so the two callers get a
#: precisely typed result instead of a union they have to narrow by hand.
_Payload = TypeVar("_Payload", bound=BaseModel)

_CREATIVE_KEY: Final[str] = "creative_plan_v1"
_SCRIPT_KEY: Final[str] = "script_generate_v1"
_STORYBOARD_KEY: Final[str] = "storyboard_generate_v1"

_MAX_TOKENS: Final[int] = 16_000

_TEXT: Final[dict[str, Any]] = {"type": "string"}

#: Hand-written for the same reason as the vision schema: structured outputs
#: require every property in `required` and `additionalProperties: false`, which
#: a Pydantic model full of defaults does not generate. Tests pin both schemas
#: against their models.
_PLAN_FIELDS: Final[tuple[str, ...]] = (
    "title",
    "concept",
    "hook",
    "core_message",
    "narrative_structure",
    "visual_direction",
    "camera_direction",
    "music_direction",
    "ending_cta",
    "risk_notes",
)

_CREATIVE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plans": {
            "type": "array",
            # Stated in the schema as well as the prompt: §16 requires exactly
            # three, and the constraint is cheaper to enforce here than to
            # discover in validation after paying for the call.
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": dict.fromkeys(_PLAN_FIELDS, _TEXT),
                "required": list(_PLAN_FIELDS),
            },
        }
    },
    "required": ["plans"],
}

_SCRIPT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sections": {
            "type": "array",
            "minItems": len(SCRIPT_SECTIONS),
            "maxItems": len(SCRIPT_SECTIONS),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    # An enum rather than a free string: a section name the
                    # model invented is the failure `ScriptDocument` would
                    # reject anyway, and catching it at the API boundary saves
                    # a round trip.
                    "section": {"type": "string", "enum": list(SCRIPT_SECTIONS)},
                    "narration": _TEXT,
                    "visual": _TEXT,
                    "duration_seconds": {"type": "number"},
                },
                "required": ["section", "narration", "visual", "duration_seconds"],
            },
        }
    },
    "required": ["sections"],
}


_STORYBOARD_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "shots": {
            "type": "array",
            "minItems": 1,
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sequence_no": {"type": "integer", "minimum": 1},
                    "title": _TEXT,
                    "shot_type": {
                        "type": "string",
                        "enum": [member.value for member in ShotType],
                    },
                    # §18's per-shot bounds, stated to the API as well as in
                    # the prompt: a constraint the vendor enforces costs
                    # nothing, and one discovered in validation costs a call.
                    "duration_seconds": {
                        "type": "number",
                        "minimum": MIN_SHOT_SECONDS,
                        "maximum": MAX_SHOT_SECONDS,
                    },
                    "visual_description": _TEXT,
                    "camera": _TEXT,
                    "motion": _TEXT,
                    "lighting": _TEXT,
                    "composition": _TEXT,
                    "voiceover": _TEXT,
                    "subtitle": _TEXT,
                    "transition_in": {
                        "type": "string",
                        "enum": [member.value for member in TransitionType],
                    },
                    "transition_out": {
                        "type": "string",
                        "enum": [member.value for member in TransitionType],
                    },
                    "reference_roles": {"type": "array", "items": _TEXT},
                },
                "required": [
                    "sequence_no",
                    "title",
                    "shot_type",
                    "duration_seconds",
                    "visual_description",
                    "camera",
                    "motion",
                    "lighting",
                    "composition",
                    "voiceover",
                    "subtitle",
                    "transition_in",
                    "transition_out",
                    "reference_roles",
                ],
            },
        }
    },
    "required": ["shots"],
}


class AnthropicLLMProvider:
    """Writes creative plans and scripts with a Claude text model."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client if client is not None else _build_client(self._settings)

    @property
    def name(self) -> str:
        return "anthropic"

    # -- creative (§16) -----------------------------------------------------

    def generate_creative_plans(self, brief: CreativeBrief) -> CreativeGeneration:
        prompt = get_prompt(_CREATIVE_KEY)
        text = prompt.render(**_brief_values(brief))

        payload, usage = self._complete(text, _CREATIVE_SCHEMA, CreativePlanSet)
        return CreativeGeneration(
            plans=payload,
            provider=self.name,
            prompt_key=prompt.key,
            prompt_version=prompt.version,
            usage=usage,
        )

    # -- script (§17) -------------------------------------------------------

    def generate_script(
        self, brief: CreativeBrief, plan: CreativePlanDraft, *, character_budget: int
    ) -> ScriptGeneration:
        prompt = get_prompt(_SCRIPT_KEY)
        text = prompt.render(
            **_brief_values(brief),
            plan_title=plan.title,
            plan_concept=plan.concept,
            plan_hook=plan.hook,
            plan_core_message=plan.core_message,
            plan_narrative=plan.narrative_structure,
            plan_cta=plan.ending_cta,
            character_budget=character_budget,
        )

        payload, usage = self._complete(text, _SCRIPT_SCHEMA, ScriptDocument)
        return ScriptGeneration(
            document=payload,
            provider=self.name,
            prompt_key=prompt.key,
            prompt_version=prompt.version,
            usage=usage,
        )

    # -- storyboard (§18) ---------------------------------------------------

    def generate_storyboard(
        self, brief: CreativeBrief, script_text: str, *, shot_count: int
    ) -> StoryboardGeneration:
        prompt = get_prompt(_STORYBOARD_KEY)
        text = prompt.render(
            **_brief_values(brief),
            script=script_text,
            shot_count=shot_count,
        )

        payload, usage = self._complete(text, _STORYBOARD_SCHEMA, StoryboardDraft)
        return StoryboardGeneration(
            storyboard=payload,
            provider=self.name,
            prompt_key=prompt.key,
            prompt_version=prompt.version,
            usage=usage,
        )

    # -- the call -----------------------------------------------------------

    def _complete(
        self,
        instructions: str,
        schema: dict[str, Any],
        model: type[_Payload],
    ) -> tuple[_Payload, ProviderUsage]:
        """Call, validate, and re-prompt once on a parse failure (§107).

        The retry sends the model's own error back to it. Re-sending the
        identical prompt would be superstition — nothing changed, so nothing
        would change — whereas naming the violation is information the model
        can act on.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": instructions}]
        attempts = self._settings.llm_parse_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            started = time.monotonic()
            response = self._send(messages, schema)
            elapsed_ms = int((time.monotonic() - started) * 1000)

            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "refusal":
                raise ProviderRejectedError(
                    "The provider declined to write this.",
                    details={"stop_reason": "refusal"},
                )
            if stop_reason == "max_tokens":
                raise ValueError("The provider's response was cut off before the JSON completed.")

            text = _first_text(response)
            try:
                return model.model_validate(json.loads(text)), _usage(response, elapsed_ms)
            except (ValueError, TypeError) as exc:
                last_error = exc
                logger.warning(
                    "llm_output_failed_validation",
                    extra={"attempt": attempt + 1, "attempts": attempts},
                )
                if attempt + 1 >= attempts:
                    break
                # §107's "retry parse": tell it precisely what was wrong.
                messages = [
                    *messages,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "That response did not satisfy the required schema. "
                            f"The validator reported: {exc}. "
                            "Return the corrected JSON object only, with no prose."
                        ),
                    },
                ]

        raise ValueError(
            f"The provider's output failed validation after {attempts} attempts: {last_error}"
        )

    def _send(self, messages: list[dict[str, Any]], schema: dict[str, Any]) -> Any:
        """Make the call, translating vendor exceptions into ours (§20)."""
        import anthropic

        try:
            return self._client.messages.create(
                model=self._settings.anthropic_llm_model,
                max_tokens=_MAX_TOKENS,
                output_config={
                    "format": {"type": "json_schema", "schema": schema},
                    "effort": self._settings.anthropic_llm_effort,
                },
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitedError("The LLM provider is rate limiting.") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError("Could not reach the LLM provider.") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailableError("The LLM provider returned a server error.") from exc
            raise ProviderRejectedError(
                "The LLM provider rejected the request.",
                details={"status_code": exc.status_code},
            ) from exc


def _brief_values(brief: CreativeBrief) -> dict[str, object]:
    """Flatten a brief into prompt placeholders.

    The two lists are rendered as bullets and, when empty, as an explicit
    statement that there are none. A blank where the facts should be reads to a
    model as an omission it might helpfully fill; "(none)" reads as a
    constraint — which is exactly what it is.
    """
    return {
        "product_name": brief.product_name,
        "category": brief.category,
        "verified_facts": _bullets(brief.verified_facts),
        "verified_claims": _bullets(brief.verified_claims),
        "visual_dna": json.dumps(brief.visual_dna, ensure_ascii=False),
        "brand_notes": brief.brand_notes or "(none)",
        "purpose": brief.purpose,
        "target_platform": brief.target_platform,
        "target_audience": brief.target_audience or "(unspecified)",
        "language": brief.language,
        "aspect_ratio": brief.aspect_ratio,
        "duration_seconds": brief.duration_seconds,
        "style": brief.style,
    }


def _bullets(items: list[str]) -> str:
    if not items:
        return "(none — you may not state any fact or claim in this category)"
    return "\n".join(f"- {item}" for item in items)


def _first_text(response: Any) -> str:
    text = next(
        (block.text for block in response.content if getattr(block, "type", None) == "text"),
        None,
    )
    if not text:
        raise ValueError("The provider returned no text content.")
    return str(text)


def _usage(response: Any, elapsed_ms: int) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    return ProviderUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        model=getattr(response, "model", None),
        latency_ms=elapsed_ms,
    )


def _build_client(settings: Settings) -> Any:
    import anthropic

    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ProviderUnavailableError(
            "ANTHROPIC_API_KEY is not configured. Set it, or run with USE_MOCK_PROVIDERS=true."
        )
    return anthropic.Anthropic(api_key=key, timeout=settings.llm_timeout_seconds)
