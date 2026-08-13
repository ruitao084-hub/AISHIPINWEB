"""Content screening (§61, P16-T13).

Uploads, prompts and generation requests pass through here before they reach a
provider, and the verdict is recorded whichever way it goes.

**Why a local screen at all, when every video provider moderates its own
input.** Three reasons. A provider's rejection arrives after the request is
billed. A rejection tells you a policy fired, not which one, so nobody can act
on it. And §14's whole argument is that this platform makes claims about real
products — screening the *script* for a medical claim is our job, not a video
model's.

The default implementation is a keyword and pattern screen. It is deliberately
modest: it catches the categories that matter for a product advertising tool
and flags rather than blocks almost everything, because a false block costs a
customer a video and a false flag costs a reviewer a minute. A hosted
moderation provider slots in behind the same interface (§20).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import ModerationDecision, ModerationTarget
from backend_core.domain.models import ModerationResult
from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger

logger = get_logger(__name__)


class ContentBlockedError(AppError):
    """Screening refused this content (§61)."""

    code = ErrorCode.PROVIDER_REJECTED
    http_status = 422
    default_message = "This content cannot be used."


@dataclass(frozen=True, slots=True)
class ModerationVerdict:
    """What a screen concluded."""

    decision: ModerationDecision
    categories: tuple[str, ...] = ()
    score: float | None = None
    excerpt: str | None = None

    @property
    def blocked(self) -> bool:
        return self.decision is ModerationDecision.BLOCKED

    @property
    def clean(self) -> bool:
        return self.decision is ModerationDecision.ALLOWED


@runtime_checkable
class ModerationProvider(Protocol):
    """§20's contract, for screening (P16-T13)."""

    @property
    def name(self) -> str: ...

    def screen_text(self, text: str, *, target: ModerationTarget) -> ModerationVerdict: ...


#: Categories the local screen knows about, as (name, pattern, blocking).
#:
#: Blocking is reserved for content that would make the platform complicit
#: rather than merely embarrassed — §14's medical and financial guarantees are
#: flagged, because a human reviewer can often confirm them from a document,
#: while the two blocking categories cannot be made acceptable by review.
_RULES: Final[tuple[tuple[str, re.Pattern[str], bool], ...]] = (
    (
        "sexual_minors",
        re.compile(
            r"\b(child|minor|underage|teen|toddler)\b.{0,40}\b(nude|naked|sexual|erotic)\b"
            r"|\b(nude|naked|sexual|erotic)\b.{0,40}\b(child|minor|underage|teen)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "weapons_instructions",
        re.compile(
            r"\b(how to (make|build|construct)|instructions? for)\b.{0,40}"
            r"\b(bomb|explosive|firearm|silencer|nerve agent)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "medical_guarantee",
        re.compile(
            r"\b(cures?|治愈|根治|treats?|prevents?)\b.{0,30}\b(cancer|covid|diabetes|癌|糖尿病)\b"
            r"|\b(100%|guaranteed|保证)\b.{0,20}\b(cure|effective|疗效)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "financial_guarantee",
        re.compile(
            r"\b(guaranteed|保证)\b.{0,20}\b(returns?|profit|income|收益|回报)\b"
            r"|\b(risk[- ]free|无风险)\b.{0,20}\b(investment|投资)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "hate_speech",
        re.compile(
            r"\b(exterminate|eradicate|kill all)\b\s+\b(jews|muslims|blacks|gays|women)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "self_harm",
        re.compile(
            r"\b(how to|best way to)\b.{0,20}\b(kill yourself|commit suicide|self[- ]harm)\b",
            re.IGNORECASE,
        ),
        True,
    ),
)


@dataclass(slots=True)
class LocalModerationProvider:
    """Pattern-based screening, no network call (P16-T13).

    Runs in-process because it must run on every prompt: a per-shot network
    round trip would add seconds to a storyboard of eight shots and would fail
    open the first time the moderation vendor had an outage.
    """

    settings: Settings = field(default_factory=get_settings)

    @property
    def name(self) -> str:
        return "local"

    def screen_text(self, text: str, *, target: ModerationTarget) -> ModerationVerdict:
        if not text.strip():
            return ModerationVerdict(decision=ModerationDecision.ALLOWED)

        matched: list[str] = []
        blocking = False
        excerpt: str | None = None

        for category, pattern, blocks in _RULES:
            found = pattern.search(text)
            if found is None:
                continue
            matched.append(category)
            blocking = blocking or blocks
            if excerpt is None:
                # A window around the match, not the whole input: a reviewer
                # needs to see what fired, and copying the entire rejected text
                # into a second table defeats declining to store it.
                start = max(0, found.start() - 40)
                excerpt = text[start : found.end() + 40][:500]

        if not matched:
            return ModerationVerdict(decision=ModerationDecision.ALLOWED)

        decision = ModerationDecision.BLOCKED if blocking else ModerationDecision.FLAGGED
        logger.info(
            "moderation_match",
            extra={
                "target": target.value,
                "decision": decision.value,
                "categories": matched,
            },
        )
        return ModerationVerdict(
            decision=decision,
            categories=tuple(matched),
            # A rule-based screen has no calibrated probability. Reporting one
            # would invite thresholds nobody could justify.
            score=None,
            excerpt=excerpt,
        )


def get_moderation_provider(settings: Settings | None = None) -> ModerationProvider:
    """§20's registry entry for moderation.

    One implementation today. The indirection is not speculative — it is what
    keeps a hosted screen from being wired into call sites when it arrives.
    """
    return LocalModerationProvider(settings=settings or get_settings())


class ModerationService:
    """Screens content and records the verdict (§61)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        provider: ModerationProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._provider = provider or get_moderation_provider(self._settings)

    async def screen(
        self,
        text: str,
        *,
        workspace_id: uuid.UUID,
        target: ModerationTarget,
        target_id: str | uuid.UUID = "",
        raise_on_block: bool = True,
    ) -> ModerationVerdict:
        """Screen `text`, record the result, and optionally refuse.

        The record is written for every outcome including `ALLOWED`. "We
        checked and it was fine" and "we never checked" look identical if only
        rejections are stored, which would make the whole step unauditable.
        """
        verdict = self._provider.screen_text(text, target=target)

        self._session.add(
            ModerationResult(
                workspace_id=workspace_id,
                target_type=target,
                target_id=str(target_id)[:64],
                decision=verdict.decision,
                provider=self._provider.name,
                categories=list(verdict.categories),
                score=verdict.score,
                excerpt=verdict.excerpt,
            )
        )

        if verdict.blocked and raise_on_block:
            raise ContentBlockedError(
                "This content cannot be used.",
                details={"categories": list(verdict.categories)},
            )
        return verdict

    async def screen_many(
        self,
        texts: dict[str, str],
        *,
        workspace_id: uuid.UUID,
        target: ModerationTarget,
        raise_on_block: bool = True,
    ) -> dict[str, ModerationVerdict]:
        """Screen several pieces at once, keyed by caller-chosen ids.

        Used for a storyboard: eight shot prompts screened in one pass, so a
        rejection names the shot rather than the storyboard.
        """
        verdicts: dict[str, ModerationVerdict] = {}
        blocked: list[str] = []

        for key, text in texts.items():
            verdict = await self.screen(
                text,
                workspace_id=workspace_id,
                target=target,
                target_id=key,
                raise_on_block=False,
            )
            verdicts[key] = verdict
            if verdict.blocked:
                blocked.append(key)

        if blocked and raise_on_block:
            raise ContentBlockedError(
                "Some of this content cannot be used.",
                details={"blocked": blocked},
            )
        return verdicts


__all__ = [
    "ContentBlockedError",
    "LocalModerationProvider",
    "ModerationProvider",
    "ModerationService",
    "ModerationVerdict",
    "get_moderation_provider",
]
