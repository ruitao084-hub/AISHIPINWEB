"""Creative plan and script schemas (§16, §17, §107, P7-T05).

§107 makes schema validation mandatory for every structured LLM output and adds
a rule worth stating on its own: **a badly formed response must not reach the
database.** These models are where that is enforced — the service validates
before it writes, so a model that returned nine sections instead of the nine
§17 names fails loudly rather than producing a half-empty script.

Both models use ``extra="forbid"``. A field the model invented is a signal that
the prompt and the schema have drifted apart, and silently dropping it hides
exactly what validation is for.
"""

from __future__ import annotations

import math
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_core.domain.enums import SCRIPT_SECTIONS, SPEECH_RATE_CHARS_PER_SECOND


class CreativePlanDraft(BaseModel):
    """One creative direction, as the model returns it (§16).

    Every field §16 lists, and nothing else. They are all required with no
    defaults: a plan missing its hook is not a plan, and accepting one would
    push the gap downstream to the script engine, which cannot fill it either.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    concept: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    core_message: str = Field(min_length=1)
    narrative_structure: str = Field(min_length=1)
    visual_direction: str = Field(min_length=1)
    camera_direction: str = Field(min_length=1)
    music_direction: str = Field(min_length=1)
    ending_cta: str = Field(min_length=1)
    #: §16's `risk_notes`. Allowed to be empty — "nothing risky here" is a
    #: legitimate answer, unlike a missing hook.
    risk_notes: str = ""


class CreativePlanSet(BaseModel):
    """Exactly the three plans §16 requires.

    Three is a hard count, not a minimum. §16 says the user chooses one of
    three, and a model that returned two would quietly narrow the choice the
    product promises.
    """

    model_config = ConfigDict(extra="forbid")

    plans: list[CreativePlanDraft] = Field(min_length=3, max_length=3)

    @field_validator("plans")
    @classmethod
    def _titles_must_differ(cls, plans: list[CreativePlanDraft]) -> list[CreativePlanDraft]:
        """Three plans that say the same thing are one plan printed thrice.

        The failure this catches is real: asked for variations, a model will
        happily produce three paraphrases of one idea. Titles are the cheapest
        proxy for "these are genuinely different directions", and a collision
        means the generation is worth retrying rather than showing.
        """
        titles = [plan.title.strip().casefold() for plan in plans]
        if len(set(titles)) != len(titles):
            raise ValueError("The three creative plans must be distinct directions.")
        return plans


class ScriptSection(BaseModel):
    """One narrated beat of the script (§17)."""

    model_config = ConfigDict(extra="forbid")

    #: One of :data:`SCRIPT_SECTIONS`. Validated below rather than typed as a
    #: Literal so the section list has exactly one definition.
    section: str
    #: What the voiceover says. May be empty for a purely visual beat.
    narration: str = ""
    #: What is on screen while it says it. Feeds PHASE 8's storyboard.
    visual: str = ""
    #: The model's own pacing intent. Advisory: the real budget is computed
    #: from the narration below, because a model's arithmetic is not evidence.
    duration_seconds: float = Field(default=0.0, ge=0)

    @field_validator("section")
    @classmethod
    def _known_section(cls, value: str) -> str:
        if value not in SCRIPT_SECTIONS:
            raise ValueError(f"Unknown script section {value!r}")
        return value


class ScriptDocument(BaseModel):
    """A complete script (§17).

    All nine sections, in §17's order, no duplicates. Order is not cosmetic —
    PHASE 8 turns this sequence into shots, and a script whose CTA precedes its
    hook would produce a storyboard nobody could film.
    """

    model_config = ConfigDict(extra="forbid")

    sections: list[ScriptSection] = Field(min_length=len(SCRIPT_SECTIONS))

    @field_validator("sections")
    @classmethod
    def _exactly_the_expected_sections_in_order(
        cls, sections: list[ScriptSection]
    ) -> list[ScriptSection]:
        names = tuple(section.section for section in sections)
        if names != SCRIPT_SECTIONS:
            raise ValueError(
                "The script must contain every section from §17 exactly once, in order. "
                f"Got: {names}"
            )
        return sections

    @property
    def plain_text(self) -> str:
        """The narration alone, in order — what a reader and the TTS engine want."""
        return "\n".join(
            section.narration.strip() for section in self.sections if section.narration.strip()
        )

    @property
    def narration_characters(self) -> int:
        """Total spoken characters, whitespace excluded.

        Whitespace is excluded because it is not spoken, and because Chinese
        narration has almost none while English has a great deal — counting it
        would make the same budget mean two different things.
        """
        return sum(len("".join(section.narration.split())) for section in self.sections)

    def estimated_duration_seconds(self, rate: float = SPEECH_RATE_CHARS_PER_SECOND) -> float:
        """How long this would take to say, at a typical delivery rate (§17)."""
        return round(self.narration_characters / rate, 2)


#: How far over or under the project's target duration a script may land before
#: it is worth telling somebody. Generous on purpose: §17 asks for a *budget*,
#: and a script rejected for being two seconds long would be a worse product
#: than one flagged as slightly long.
DURATION_TOLERANCE: Final[float] = 0.35


def character_budget(duration_seconds: int, rate: float = SPEECH_RATE_CHARS_PER_SECOND) -> int:
    """How many spoken characters fit in ``duration_seconds`` (§17).

    Given to the model in the prompt. Floor rather than round: a script that
    runs long has to be cut, and cutting is the edit users resent most.
    """
    return math.floor(duration_seconds * rate)


def duration_fits(estimated: float, target: int, tolerance: float = DURATION_TOLERANCE) -> bool:
    """Whether an estimated narration length is close enough to the target."""
    if target <= 0:
        return False
    return abs(estimated - target) / target <= tolerance
