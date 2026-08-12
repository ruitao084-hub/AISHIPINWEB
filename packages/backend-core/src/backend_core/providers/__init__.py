"""AI provider adapters (§20).

One Protocol per capability — LLM, Vision, Image, Video, TTS, Moderation —
each with a real implementation and a Mock counterpart (§21, §172).

An adapter maps parameters, calls the vendor, maps status, maps errors and
reports cost metadata. It must NOT change project state, touch credits, write
storyboards or render video. Holding that line is what makes swapping a vendor
a configuration change rather than a rewrite.

Vision landed in PHASE 6 (§14, ADR-0009); video follows in PHASE 9.

Nothing is re-exported here on purpose. `registry.get_vision_provider` is
the only supported way to obtain a provider — a convenient
`from backend_core.providers import AnthropicVisionProvider` would be a
shortcut straight past the feature flags §170 depends on.
"""
