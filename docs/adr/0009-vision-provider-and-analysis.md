# 9. Vision provider adapter, prompt registry and the analysis boundary

Date: 2026-08-12
Status: Accepted
Phase: 6 (§14, §15, §20, §109, §170, §172)

## Context

PHASE 6 puts a language model into the product pipeline for the first time. The
model looks at a customer's photographs and produces a structured description
that the rest of the platform will eventually turn into advertising.

That makes it the highest-consequence component built so far. §0.1 rule 14 and
§13 both say the same thing from different directions: the platform must not
state a product parameter nobody confirmed. A vision model will confidently
report a filtration rating it inferred from a product's category, and the
sentence it writes will be indistinguishable from one it read off the box.

Three separate decisions follow, and they are recorded together because each is
only safe given the others.

## Decision

### 1. The observed/inferred boundary is structural, not a flag

`ProductIntelligence` (§14) splits the model's output by field:

- `colors`, `materials`, `visible_text`, `structural_features`,
  `visual_features` — what is **visible in the photograph**;
- `possible_use_cases`, `possible_selling_points` — the model's
  **speculation**, which §109 forbids using as factual advertising.

`OBSERVED_FIELDS` and `INFERRED_FIELDS` name those two sets, and
`ProductAnalysisService._fact_specs` iterates `OBSERVED_FIELDS` only. The
inferred fields are therefore structurally unable to reach `create_fact` —
there is no code path from one to the other, rather than a check somebody could
forget to write.

The alternative — a per-item `is_observed` boolean the model sets — was
rejected. It puts the safety property in the hands of the thing being
constrained: a model that hallucinates a material will equally happily
hallucinate that it observed it.

Everything observed still arrives `AI_INFERRED`. Nothing is auto-verified,
including `visible_text`, which is the field most likely to be trusted and the
one OCR most often gets subtly wrong. A misread model number is a wrong
product, not a small error.

### 2. Prompts live in a versioned code registry, not the database

§15 requires every call to record the prompt key and version. `backend_core.prompts.registry`
holds the text; `ProductAnalysis` records what was used.

Prompts are in code rather than in §10.18's `prompt_versions` table because a
prompt's wording and the schema its output must satisfy change _together_ — so
shipping them in one atomic deploy is what keeps them consistent. The table
becomes worthwhile when prompts need to change without a deploy, which is when
A/B testing lands (PHASE 20), not before.

`Prompt.render` substitutes `{{name}}` placeholders in a **single pass**.
`str.format` would choke on the JSON schema example the prompt contains, and a
loop of `str.replace` would let a product named `{{language}}` — untrusted
input, §108 — be substituted into the template and then expanded by the next
iteration.

### 3. One `VisionProvider` Protocol, two implementations, selected by config

§0.1 rule 12 forbids hardcoding a vendor into core business logic. Business
code calls `get_vision_provider(settings)` and receives something satisfying
the `VisionProvider` Protocol; it never names a vendor.

`MockVisionProvider` is deterministic (seeded from the product name and image
bytes) and reaches every failure branch through `MOCK_VISION_MODE` (§172):
`unavailable`, `rate_limited`, `rejected`, `malformed`, `empty`. The five modes
are not decoration — each maps to a _different_ decision in the caller, and the
one that matters most is `malformed`: a 200 whose body fails schema validation
means the provider is up and answering, so it is deliberately a plain
`ValueError` rather than a `ProviderUnavailableError`, keeping it out of the
retry path where an identical request would fail identically while costing
money again.

The mock deliberately returns `visible_text: []`. It has no real image to read,
and inventing legible text would be exactly the fabrication §13 forbids, in the
field a reviewer is most likely to trust.

`AnthropicVisionProvider` targets the Messages API with
`output_config.format` structured outputs. Selection requires
`USE_MOCK_PROVIDERS=false`, `ENABLE_REAL_VISION_PROVIDER=true` and a key —
three separate switches, because §170 requires the whole flow to work on mocks
and a silent fallback to a paid provider would defeat that.

### 4. Analysis is synchronous, for now

§83 permits a short synchronous task. The product moves
`ASSETS_READY → ANALYZING → REVIEW_REQUIRED` inside one transaction, so both
edges of §104 are honoured even though the intermediate state is not separately
observable — which is honest for a call that takes seconds. On failure the
transaction rolls back and the product stays where it was (§24: a failure must
not strand an entity mid-state).

PHASE 9 moves this behind the job queue, at which point `ANALYZING` becomes a
state a client can actually poll. The API shape already anticipates that: the
endpoint returns a `ProductAnalysis` row rather than the intelligence directly.

The endpoint requires `GENERATION_RUN`, not `PRODUCT_WRITE`. §40 deliberately
does not let write access imply spending the workspace's money.

## Consequences

**Good.** The safety property is checkable by reading one function. Swapping
vendors is an environment change. Every result is traceable to a prompt version
and an image set. The entire flow runs with no API key, so PHASE 7 is not
blocked on procurement.

**Costs.** The JSON schema sent to the vendor is hand-written rather than
derived from the Pydantic model — a model whose fields all have defaults
generates a schema the structured-output API rejects. Two artefacts must
therefore stay in agreement, and a test asserts they do
(`test_the_schema_matches_the_pydantic_model`).

**The gap, stated plainly.** `AnthropicVisionProvider` has never run against
the live API. Nobody has supplied a key. Request construction, image
downscaling, response parsing and every error-mapping branch are tested against
a stubbed client; whether the request shape is the one the vendor actually
accepts is not, and cannot be until a key exists. This is recorded in the
module's own docstring, in TASK_STATUS, and here, because "it compiles" is not
"it works" and the distinction should not have to be rediscovered.

## Alternatives considered

| Option                                            | Why not                                                                                                                                                            |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Let the model mark each item observed vs inferred | Puts the safety property inside the thing being constrained. A model that fabricates a value will fabricate its provenance.                                        |
| Auto-verify high-confidence observations          | §13 admits no confidence threshold. A model's confidence is not evidence, and the failure mode is a confidently wrong product parameter.                           |
| Prompts in the `prompt_versions` table now        | Prompt text and output schema change together; splitting their deploys is how they drift. Revisit for A/B testing (PHASE 20).                                      |
| Generate the vendor JSON schema from Pydantic     | Produces a schema the structured-output API rejects (defaults ⇒ nothing `required`). Hand-written and test-pinned is the smaller cost.                             |
| Send presigned URLs instead of image bytes        | A presigned URL to our own bucket is a credential. Handing one to a third party leaks it and makes the call depend on our storage reachability from their network. |
| Make analysis a job immediately                   | The job system is PHASE 9. Building half of it here would be cross-phase development, and §83 explicitly allows the synchronous form.                              |
