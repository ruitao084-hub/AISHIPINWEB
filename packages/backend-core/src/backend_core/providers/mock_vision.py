"""Mock vision provider (taskbook §170, §172, P6-T04).

Two jobs, and the second is the one usually skipped.

**It lets the whole flow run with no API key.** §170 requires the end-to-end
path to work on mocks so development does not stall waiting on credentials.

**It makes the failure paths reachable.** §172 asks for deliberate failure
injection, because the interesting code is the error handling and a mock that
only ever succeeds leaves all of it unexercised. `MOCK_VISION_MODE` selects
unavailable, rate-limited, rejected, malformed output, or an empty result —
each mapping to a distinct branch the caller has to get right.

Output is **deterministic**, seeded from the product name and image bytes. A
mock that returns random content makes every test that asserts on its output
flaky, and a flaky test gets deleted.
"""

from __future__ import annotations

import hashlib
import time
from typing import Final

from backend_core.config import Settings, get_settings
from backend_core.errors import (
    ProviderRateLimitedError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from backend_core.observability import get_logger
from backend_core.prompts.registry import active_version
from backend_core.providers.base import (
    ProviderImage,
    ProviderUsage,
    VisionAnalysis,
)
from backend_core.providers.schemas import ProductIntelligence, VisualDNA

logger = get_logger(__name__)

_PROMPT_KEY: Final[str] = "product_analyze_v1"

# Small pools the mock draws from deterministically. Plausible enough that the
# review UI can be judged on real-looking content, and varied enough that two
# different products do not produce identical output.
_COLORS: Final[tuple[str, ...]] = ("哑光白", "深空灰", "香槟金", "墨绿", "浅木色")
_MATERIALS: Final[tuple[str, ...]] = ("ABS 工程塑料", "阳极氧化铝", "钢化玻璃", "实木", "硅胶")
_STRUCTURE: Final[tuple[str, ...]] = (
    "圆角矩形机身",
    "顶部出风格栅",
    "底部防滑脚垫",
    "隐藏式提手",
    "可拆卸后盖",
)
_VISUAL: Final[tuple[str, ...]] = (
    "低饱和配色",
    "无缝一体成型外观",
    "正面显示屏",
    "环形指示灯",
    "细窄边框",
)
_USE_CASES: Final[tuple[str, ...]] = (
    "客厅日常使用",
    "卧室夜间使用",
    "小型办公室",
    "出租屋改善空气",
)
_SELLING_POINTS: Final[tuple[str, ...]] = (
    "外观简洁，容易融入家居环境",
    "操作面板直观",
    "体积紧凑，便于摆放",
)
_TONE: Final[tuple[str, ...]] = ("干净", "温暖", "现代", "安静")
_BACKGROUNDS: Final[tuple[str, ...]] = ("浅色木质台面", "素色墙面", "自然光窗边")
_CAMERA: Final[tuple[str, ...]] = ("缓慢环绕", "固定中景", "微距细节推进")


class MockVisionProvider:
    """A deterministic stand-in for a real vision model."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "mock"

    def analyze_product(
        self,
        images: list[ProviderImage],
        *,
        product_name: str | None = None,
        category: str | None = None,
        language: str = "zh-CN",
    ) -> VisionAnalysis:
        started = time.monotonic()
        mode = self._settings.mock_vision_mode

        self._maybe_fail(mode)

        if not images:
            raise ProviderRejectedError("No images were supplied for analysis.")

        seed = _seed(product_name, images)

        if mode == "empty":
            # A well-formed but empty answer. Distinct from an error: the
            # provider worked, saw nothing it could describe, and said so.
            intelligence = ProductIntelligence(
                uncertain_fields=[
                    "product_name",
                    "category",
                    "brand",
                    "colors",
                    "materials",
                ]
            )
        else:
            intelligence = _build(seed, product_name, category)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "mock_vision_analysis",
            extra={"image_count": len(images), "mode": mode, "language": language},
        )

        return VisionAnalysis(
            intelligence=intelligence,
            provider=self.name,
            prompt_key=_PROMPT_KEY,
            prompt_version=active_version(_PROMPT_KEY),
            usage=ProviderUsage(
                input_tokens=len(images) * 1_500,
                output_tokens=420,
                model="mock-vision-1",
                latency_ms=elapsed_ms,
            ),
            raw={"mock": True, "mode": mode, "seed": seed},
        )

    def _maybe_fail(self, mode: str) -> None:
        """Inject the failure the configuration asked for (§172)."""
        if mode == "unavailable":
            raise ProviderUnavailableError("Mock vision provider is unavailable.")
        if mode == "rate_limited":
            raise ProviderRateLimitedError("Mock vision provider is rate limiting.")
        if mode == "rejected":
            raise ProviderRejectedError("Mock vision provider rejected the request.")
        if mode == "malformed":
            # Deliberately not a provider error: this is the case where a
            # provider returns 200 with output that does not match the schema,
            # which is the failure §14's validation exists to catch and which a
            # caller must not mistake for a transport problem.
            raise ValueError("Mock vision provider returned unparseable output.")


def _seed(product_name: str | None, images: list[ProviderImage]) -> str:
    """Stable hash over the inputs, so the same product analyses the same way."""
    digest = hashlib.sha256()
    digest.update((product_name or "").encode())
    for image in images:
        digest.update(hashlib.sha256(image.data).digest())
    return digest.hexdigest()[:16]


def _pick(pool: tuple[str, ...], seed: str, offset: int, count: int) -> list[str]:
    """Deterministically choose ``count`` distinct entries from ``pool``."""
    start = int(seed[offset % len(seed) : (offset % len(seed)) + 2] or "0", 16)
    return [pool[(start + step) % len(pool)] for step in range(min(count, len(pool)))]


def _build(seed: str, product_name: str | None, category: str | None) -> ProductIntelligence:
    return ProductIntelligence(
        product_name=product_name or "未命名产品",
        category=category or "家用电器",
        brand="",
        colors=_pick(_COLORS, seed, 0, 2),
        materials=_pick(_MATERIALS, seed, 2, 2),
        # Left empty on purpose: the mock has no real image to read, and
        # inventing legible text would be exactly the fabrication §13 forbids —
        # in the one field most likely to be trusted as evidence.
        visible_text=[],
        structural_features=_pick(_STRUCTURE, seed, 4, 3),
        visual_features=_pick(_VISUAL, seed, 6, 3),
        possible_use_cases=_pick(_USE_CASES, seed, 8, 2),
        possible_selling_points=_pick(_SELLING_POINTS, seed, 10, 2),
        # Honest about what a mock cannot know. Also gives the review UI
        # something real to render for the uncertain case.
        uncertain_fields=["brand", "visible_text"],
        visual_dna=VisualDNA(
            tone=_pick(_TONE, seed, 12, 2),
            palette=_pick(_COLORS, seed, 0, 3),
            recommended_backgrounds=_pick(_BACKGROUNDS, seed, 14, 2),
            recommended_camera_styles=_pick(_CAMERA, seed, 1, 2),
        ),
    )
