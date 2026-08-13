"""TTS provider contract and mock (§30, PHASE 12).

§30 fixes the interface — text, language, voice, speed, style in; audio asset,
duration, provider metadata and cost out — and the MVP floor: Chinese and
English, male and female.

The **duration** in that output is the point. §31 builds subtitles from TTS
durations and §33's timeline places clips against them, so a synthesiser that
returned audio without a measured length would leave every downstream timing
to be guessed. The mock measures its own output rather than predicting it, for
the same reason the real one must.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend_core.config import Settings, get_settings
from backend_core.domain.enums import VoiceGender
from backend_core.errors import ProviderRejectedError, ProviderUnavailableError
from backend_core.observability import get_logger

logger = get_logger(__name__)

#: 16 kHz mono is the floor §30's "high quality" allows once the audio is
#: normalised and muxed; higher rates cost bandwidth the finished MP4 discards.
_SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """§30's documented inputs."""

    text: str
    language: str = "zh-CN"
    voice: str = "default"
    gender: VoiceGender = VoiceGender.FEMALE
    #: 1.0 is the voice's natural pace. Bounded by the caller, not here — a
    #: provider that silently clamped would make the timeline's arithmetic
    #: wrong without saying so.
    speed: float = 1.0
    style: str = ""


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """§30's documented outputs."""

    audio: bytes
    mime_type: str
    #: Measured, never estimated. §31 and §33 both build on this number.
    duration_ms: int
    provider: str
    voice: str
    cost: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TTSProvider(Protocol):
    """Turns narration into audio (§30)."""

    @property
    def name(self) -> str: ...

    def synthesize(self, request: SpeechRequest) -> SpeechResult: ...


class MockTTSProvider:
    """Generates real, playable silence of a plausible length (§170).

    Silence rather than a tone: the render pipeline treats this as narration
    audio, and a 30-second sine wave under a product video is worse to review
    than nothing. The *length* is what matters downstream, and that is
    computed from the text at a per-language rate.

    It emits a genuine WAV, not a placeholder — ffprobe, loudness
    normalisation and the muxer all run against it in development, so the
    render path is exercised rather than stubbed.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "mock"

    def synthesize(self, request: SpeechRequest) -> SpeechResult:
        if not request.text.strip():
            raise ProviderRejectedError("There is nothing to synthesise.")
        if request.speed <= 0:
            raise ProviderRejectedError("Speech speed must be positive.")

        duration_ms = estimate_duration_ms(
            request.text, language=request.language, speed=request.speed
        )
        audio = _silent_wav(duration_ms)

        logger.info(
            "mock_tts_synthesized",
            extra={
                "chars": len(request.text),
                "duration_ms": duration_ms,
                "language": request.language,
            },
        )
        return SpeechResult(
            audio=audio,
            mime_type="audio/wav",
            duration_ms=duration_ms,
            provider=self.name,
            voice=f"{request.language}-{request.gender.value.lower()}",
            raw={"mock": True},
        )


def estimate_duration_ms(text: str, *, language: str = "zh-CN", speed: float = 1.0) -> int:
    """How long this text takes to say.

    Per-language rates, because the unit differs: Chinese is counted in
    characters and English in words, and using one rate for both makes an
    English script three times too long.
    """
    stripped = "".join(text.split())
    if language.lower().startswith("zh"):
        units = len(stripped)
        per_second = 4.5
    else:
        units = max(1, len(text.split()))
        per_second = 2.6

    seconds = units / (per_second * max(0.1, speed))
    return max(200, math.ceil(seconds * 1000))


def _silent_wav(duration_ms: int) -> bytes:
    """A valid 16-bit mono WAV of the requested length.

    Written by hand rather than pulled from a fixture so the length is exact —
    the timeline places clips against this number, and a fixture rounded to the
    nearest second would drift a 40-second video by a visible amount.
    """
    frames = int(_SAMPLE_RATE * duration_ms / 1000)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, _SAMPLE_RATE, _SAMPLE_RATE * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def get_tts_provider(settings: Settings | None = None) -> TTSProvider:
    """The configured synthesiser (§20, §170)."""
    resolved = settings or get_settings()
    if resolved.use_mock_providers or resolved.tts_provider == "mock":
        return MockTTSProvider(resolved)
    raise ProviderUnavailableError(
        f"No real TTS provider is implemented for {resolved.tts_provider!r}. "
        "Set TTS_PROVIDER=mock or USE_MOCK_PROVIDERS=true."
    )


def voice_for(language: str, gender: VoiceGender) -> str:
    """A stable voice identifier for §30's four MVP combinations."""
    digest = hashlib.sha256(f"{language}:{gender.value}".encode()).hexdigest()[:6]
    return f"{language.lower()}-{gender.value.lower()}-{digest}"


__all__ = [
    "MockTTSProvider",
    "SpeechRequest",
    "SpeechResult",
    "TTSProvider",
    "estimate_duration_ms",
    "get_tts_provider",
    "voice_for",
]
