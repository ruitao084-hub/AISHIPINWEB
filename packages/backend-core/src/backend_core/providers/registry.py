"""Provider selection (taskbook §20, §0.1 rule 6, §122).

The single place a provider name becomes a provider object. §0.1 forbids
hardcoding any one model into core business logic, and the practical form of
that rule is this: business code asks for "the vision provider", never for a
vendor.

Selection is configuration, not code — so switching providers, or falling back
to the mock while a vendor is down, is an environment change (§122's feature
flags) rather than a deploy.
"""

from __future__ import annotations

from backend_core.config import Settings, get_settings
from backend_core.errors import ProviderUnavailableError
from backend_core.observability import get_logger
from backend_core.providers.base import LLMProvider, VisionProvider
from backend_core.providers.mock_llm import MockLLMProvider
from backend_core.providers.mock_vision import MockVisionProvider

logger = get_logger(__name__)


def get_vision_provider(settings: Settings | None = None) -> VisionProvider:
    """Build the configured vision provider.

    ``USE_MOCK_PROVIDERS`` wins over everything else, so a developer cannot
    accidentally spend money because a key happened to be present in their
    environment. A real provider additionally needs its own feature flag *and*
    a key: §170 requires the whole flow to work on mocks, and silently falling
    back to a real provider would defeat that.
    """
    resolved = settings or get_settings()

    if resolved.use_mock_providers or resolved.default_vision_provider == "mock":
        return MockVisionProvider(resolved)

    if not resolved.enable_real_vision_provider:
        raise ProviderUnavailableError(
            "A real vision provider is configured but ENABLE_REAL_VISION_PROVIDER is off."
        )

    name = resolved.default_vision_provider
    if name == "anthropic":
        # Imported lazily so the mock path never needs the HTTP client, and so
        # a missing optional dependency cannot break a mock-only deployment.
        from backend_core.providers.anthropic_vision import AnthropicVisionProvider

        return AnthropicVisionProvider(resolved)

    raise ProviderUnavailableError(f"Unknown vision provider: {name!r}")


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Build the configured text provider (§16, §17).

    Same three-switch rule as the vision provider, for the same reason: §170
    requires the whole flow to work on mocks, so reaching a paid provider takes
    `USE_MOCK_PROVIDERS=false`, its own feature flag *and* a key. A missing one
    is an error, never a silent fallback in either direction.
    """
    resolved = settings or get_settings()

    if resolved.use_mock_providers or resolved.default_llm_provider == "mock":
        return MockLLMProvider(resolved)

    if not resolved.enable_real_llm_provider:
        raise ProviderUnavailableError(
            "A real LLM provider is configured but ENABLE_REAL_LLM_PROVIDER is off."
        )

    name = resolved.default_llm_provider
    if name == "anthropic":
        from backend_core.providers.anthropic_llm import AnthropicLLMProvider

        return AnthropicLLMProvider(resolved)

    raise ProviderUnavailableError(f"Unknown LLM provider: {name!r}")
