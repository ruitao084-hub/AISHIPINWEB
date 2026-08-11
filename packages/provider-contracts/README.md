# `provider-contracts`

Language-neutral capability declarations for every AI provider, so the router
and the UI can reason about what a model can do **without** importing a Python
adapter.

Taskbook §140 is explicit that the router may not assume all models are alike:

```json
{
  "provider": "",
  "model": "",
  "text_to_video": true,
  "image_to_video": true,
  "reference_images": true,
  "max_reference_images": 1,
  "durations": [],
  "ratios": [],
  "resolutions": [],
  "audio": false,
  "cancel": true,
  "webhook": false
}
```

These schemas are consumed by:

- `backend_core.providers` — validates each adapter declares its real
  capabilities, and the router matches jobs to models against them (§55).
- `apps/web` — hides options a selected provider cannot honour, rather than
  letting a user request a 16:9 job from a vertical-only model.

## Status

Placeholder. Filled in **PHASE 9 (P9-T09)** alongside the mock video provider,
then extended in **PHASE 19** when the multi-provider router needs the full
capability matrix. Writing the schemas before a single adapter exists would be
guessing at the shape.
