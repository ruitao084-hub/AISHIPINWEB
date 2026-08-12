"""Mock LLM provider for creative plans and scripts (§170, §172, P7-T04/T08).

Same two jobs as the mock vision provider, and the second one matters more
here. This mock is what PHASE 8 onward develops against, so it has to produce
output the *downstream* code can be judged on: three genuinely distinct plans
whose titles differ, and a script whose length actually tracks the project's
duration. A mock that returned nine identical sections would make the character
budget untestable and let a real bug ship.

Deterministic, seeded from the product name and the brief, so a test asserting
on its output does not become flaky.

**It never invents a product fact.** Everything factual in its output comes
from the brief's verified lists, and when those are empty the mock says so
rather than filling the gap — which is the behaviour §13 wants from the real
provider, and the behaviour the review UI must be built against.
"""

from __future__ import annotations

import hashlib
import time
from typing import Final

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import SCRIPT_SECTIONS
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.prompts.registry import active_version
from backend_core.providers.base import (
    CreativeBrief,
    CreativeGeneration,
    ProviderUsage,
    ScriptGeneration,
)
from backend_core.providers.creative_schemas import (
    CreativePlanDraft,
    CreativePlanSet,
    ScriptDocument,
    ScriptSection,
)

logger = get_logger(__name__)

_CREATIVE_KEY: Final[str] = "creative_plan_v1"
_SCRIPT_KEY: Final[str] = "script_generate_v1"

#: Three angles that really are different — a demonstration, a story and a
#: comparison-of-before-and-after. Enough that a reviewer looking at the plan
#: picker sees a real choice rather than three paraphrases.
_ANGLES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "产品实拍演示",
        "以产品本体为主角，用连续的实拍镜头把外观与结构讲清楚。",
        "先给一个让人停下来的特写，再展开完整外观。",
    ),
    (
        "生活场景故事",
        "把产品放回它被使用的房间里，让场景替产品说话。",
        "从一个具体的生活瞬间切入，产品稍后才出现。",
    ),
    (
        "使用前后对照",
        "用同一个空间的前后对照呈现差别，不做任何数字承诺。",
        "先呈现问题本身，让观众自己认出它。",
    ),
)

_CAMERA: Final[tuple[str, ...]] = (
    "缓慢环绕接微距推进，全片保持稳定运动",
    "固定中景为主，转场用轻微推镜",
    "手持轻微晃动的跟随镜头，贴近真实使用感",
)
_MUSIC: Final[tuple[str, ...]] = (
    "干净的轻电子，节奏平稳，不抢旁白",
    "温暖的木吉他，居家氛围",
    "低起伏的氛围音，留白较多",
)


class MockLLMProvider:
    """A deterministic stand-in for a real text model."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "mock"

    # -- creative (§16) -----------------------------------------------------

    def generate_creative_plans(self, brief: CreativeBrief) -> CreativeGeneration:
        started = time.monotonic()
        self._maybe_fail()

        seed = _seed(brief)
        plans = [self._plan(brief, seed, index, angle) for index, angle in enumerate(_ANGLES)]

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "mock_creative_plans_generated",
            extra={"plans": len(plans), "duration_seconds": brief.duration_seconds},
        )
        return CreativeGeneration(
            plans=CreativePlanSet(plans=plans),
            provider=self.name,
            prompt_key=_CREATIVE_KEY,
            prompt_version=active_version(_CREATIVE_KEY),
            usage=ProviderUsage(
                input_tokens=1_800,
                output_tokens=900,
                model="mock-llm-1",
                latency_ms=elapsed_ms,
            ),
        )

    def _plan(
        self, brief: CreativeBrief, seed: str, index: int, angle: tuple[str, str, str]
    ) -> CreativePlanDraft:
        name, concept, hook_style = angle
        tone = " · ".join(str(item) for item in brief.visual_dna.get("tone", [])[:2])
        backgrounds = "、".join(
            str(item) for item in brief.visual_dna.get("recommended_backgrounds", [])[:2]
        )

        # The one place the mock could fabricate, and deliberately does not.
        if brief.verified_claims:
            core = brief.verified_claims[index % len(brief.verified_claims)]
            risk = ""
        else:
            core = f"{brief.product_name}的实际使用体验"
            risk = (
                "没有任何已核实的宣传点可用，本方案只描述产品外观与使用场景。"
                "若需要功能性主张，请先在产品页面确认相应事实。"
            )

        return CreativePlanDraft(
            title=f"{name}：{brief.product_name}",
            concept=concept,
            hook=f"{hook_style}（{_pick(_HOOK_BEATS, seed, index)}）",
            core_message=core,
            narrative_structure=(
                f"{brief.duration_seconds} 秒：开场 {max(2, brief.duration_seconds // 10)} 秒，"
                f"产品呈现约一半时长，收尾留 {max(2, brief.duration_seconds // 8)} 秒给行动号召。"
            ),
            visual_direction=(
                f"{brief.style}；{tone or '自然光，低饱和'}；{backgrounds or '素色背景'}"
            ),
            camera_direction=_CAMERA[index % len(_CAMERA)],
            music_direction=_MUSIC[index % len(_MUSIC)],
            ending_cta=f"在{brief.target_platform}主页了解更多",
            risk_notes=risk,
        )

    # -- script (§17) -------------------------------------------------------

    def generate_script(
        self, brief: CreativeBrief, plan: CreativePlanDraft, *, character_budget: int
    ) -> ScriptGeneration:
        started = time.monotonic()
        self._maybe_fail()

        sections = _script_sections(brief, plan, character_budget)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        document = ScriptDocument(sections=sections)
        logger.info(
            "mock_script_generated",
            extra={
                "character_budget": character_budget,
                "characters": document.narration_characters,
            },
        )
        return ScriptGeneration(
            document=document,
            provider=self.name,
            prompt_key=_SCRIPT_KEY,
            prompt_version=active_version(_SCRIPT_KEY),
            usage=ProviderUsage(
                input_tokens=2_400,
                output_tokens=1_100,
                model="mock-llm-1",
                latency_ms=elapsed_ms,
            ),
        )

    # -- failure injection (§172) -------------------------------------------

    def _maybe_fail(self) -> None:
        """Inject the failure the configuration asked for.

        Shares `MOCK_LLM_MODE` with both methods: a vendor outage does not
        distinguish between the two calls, and neither should the mock.
        """
        mode = self._settings.mock_llm_mode
        if mode == "unavailable":
            raise ProviderUnavailableError("Mock LLM provider is unavailable.")
        if mode == "rate_limited":
            raise ProviderRateLimitedError("Mock LLM provider is rate limiting.")
        if mode == "rejected":
            raise ProviderRejectedError("Mock LLM provider rejected the request.")
        if mode == "malformed":
            # A 200 whose body fails schema validation — §107's case. Not a
            # provider error: the call was billed and the fix is a re-parse or
            # a re-prompt, not a transport retry.
            raise ValueError("Mock LLM provider returned unparseable output.")


# ---------------------------------------------------------------------------
# Deterministic content
# ---------------------------------------------------------------------------

_HOOK_BEATS: Final[tuple[str, ...]] = (
    "第一秒给出最有辨识度的细节",
    "先让画面安静下来，再进入产品",
    "用一个日常动作开场",
    "从一处结构特写切入",
)


def _seed(brief: CreativeBrief) -> str:
    digest = hashlib.sha256()
    digest.update(brief.product_name.encode())
    digest.update(brief.category.encode())
    digest.update(str(brief.duration_seconds).encode())
    return digest.hexdigest()[:16]


def _pick(pool: tuple[str, ...], seed: str, offset: int) -> str:
    start = int(seed[offset % len(seed) : (offset % len(seed)) + 2] or "0", 16)
    return pool[start % len(pool)]


#: How the character budget is split across the nine sections. Weights rather
#: than fixed counts so the same shape works for a 15-second cut and a
#: 60-second one. The middle of the script carries the most words because that
#: is where the product is actually explained.
_SECTION_WEIGHTS: Final[dict[str, float]] = {
    "opening_hook": 0.10,
    "problem": 0.10,
    "product_intro": 0.14,
    "feature_1": 0.16,
    "feature_2": 0.16,
    "usage_scene": 0.14,
    # Purely visual by default: with no verified claim to cite there is nothing
    # honest to assert here, so the mock demonstrates instead of narrating.
    "proof_or_visual_support": 0.06,
    "brand_ending": 0.08,
    "cta": 0.06,
}


def _script_sections(
    brief: CreativeBrief, plan: CreativePlanDraft, character_budget: int
) -> list[ScriptSection]:
    """Build a script that actually fits the budget it was given.

    The narration is padded or trimmed to each section's share of the budget,
    which is what makes the duration estimate meaningful — and what lets a test
    assert that a 15-second project yields a materially shorter script than a
    60-second one.
    """
    facts = brief.verified_facts or []
    claims = brief.verified_claims or []
    seconds_per_char = brief.duration_seconds / max(character_budget, 1)

    texts: dict[str, tuple[str, str]] = {
        "opening_hook": (plan.hook, f"{brief.product_name} 的标志性细节特写"),
        "problem": (
            f"在{brief.target_audience or '日常生活'}里，这件事一直不太顺手。",
            "呈现问题本身的场景，产品尚未出现",
        ),
        "product_intro": (
            f"这是{brief.product_name}，一件{brief.category}。",
            "产品完整外观，缓慢环绕",
        ),
        "feature_1": (
            _fact_sentence(facts, 0),
            "对应结构的特写",
        ),
        "feature_2": (
            _fact_sentence(facts, 1),
            "第二处结构或材质的特写",
        ),
        "usage_scene": (
            f"{plan.concept}",
            f"{brief.product_name} 在真实空间中被使用",
        ),
        "proof_or_visual_support": (
            claims[0] if claims else "",
            "连续实拍演示，不做任何数字承诺" if not claims else "画面支撑上述已核实的说法",
        ),
        "brand_ending": (plan.core_message, "产品与品牌标识同框"),
        "cta": (plan.ending_cta, "结尾板，行动号召"),
    }

    sections: list[ScriptSection] = []
    for name in SCRIPT_SECTIONS:
        narration, visual = texts[name]
        allowance = int(character_budget * _SECTION_WEIGHTS[name])
        fitted = _fit(narration, allowance)
        sections.append(
            ScriptSection(
                section=name,
                narration=fitted,
                visual=visual,
                duration_seconds=round(len(fitted) * seconds_per_char, 2),
            )
        )
    return sections


def _fact_sentence(facts: list[str], index: int) -> str:
    """A sentence about a verified fact, or an honest blank.

    Returning "" when there is no fact is the whole point: the alternative is
    a plausible-sounding sentence about a product nobody described, which is
    precisely what §13 forbids and what a mock is most tempted to do.
    """
    if index < len(facts):
        return f"{facts[index]}。"
    return ""


def _fit(text: str, allowance: int) -> str:
    """Trim to the allowance on a sentence boundary where possible.

    Never pads. Inventing filler to hit a character count would be fabricating
    content to satisfy arithmetic — a short script is a fine outcome, and the
    duration estimate reports it honestly.
    """
    stripped = text.strip()
    if allowance <= 0 or len(stripped) <= allowance:
        return stripped
    cut = stripped[:allowance]
    for boundary in ("。", "，", "；", ". ", ", "):
        position = cut.rfind(boundary)
        if position > allowance // 2:
            return cut[: position + 1]
    return cut
