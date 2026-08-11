"""AI provider adapters (§20).

One Protocol per capability — LLM, Vision, Image, Video, TTS, Moderation —
each with a real implementation and a Mock counterpart (§21, §172).

An adapter maps parameters, calls the vendor, maps status, maps errors and
reports cost metadata. It must NOT change project state, touch credits, write
storyboards or render video. Holding that line is what makes swapping a vendor
a configuration change rather than a rewrite.

Populated from PHASE 6 (vision) and PHASE 9 (video).
"""
