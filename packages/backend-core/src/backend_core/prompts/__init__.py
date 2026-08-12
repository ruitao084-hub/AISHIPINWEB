"""Prompt registry (§15).

Every prompt has a key and a version, and every call records which it used, so
a result can be traced back to the exact prompt that produced it. Versions are
immutable — a change creates a new one (§168), which is what makes A/B testing
and rollback possible.

Product text and OCR output are untrusted content (§108): any template
embedding them states that they are data, not instructions.

Populated from PHASE 6. See :mod:`backend_core.prompts.registry`.
"""
