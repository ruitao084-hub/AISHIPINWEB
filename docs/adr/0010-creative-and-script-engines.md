# 10. Creative and script engines, and the verified-claim boundary

Date: 2026-08-12
Status: Accepted
Phase: 7 (§16, §17, §105, §107, §109)

## Context

PHASE 7 is where the platform starts writing sentences a customer will publish.
The creative engine proposes three directions; the script engine turns the
chosen one into nine narrated beats. Both call a language model, and both are
one careless line away from advertising something nobody confirmed.

§109 states the rule plainly: only `VERIFIED` claims may be used. §17 repeats it
from the script's side. The question this ADR answers is not _whether_ to
enforce that — it is _where_, such that the enforcement cannot be bypassed by
someone who has not read this document.

## Decision

### 1. One function is the only door to product content

`CreativeService._brief` builds the `CreativeBrief` that both generators
receive. It is the single place either engine learns anything about the
product, and it obtains claims by calling the Truth Layer's
`get_verified_claims` — the accessor PHASE 5 built for exactly this, which
returns `VERIFIED` and nothing else. Facts are filtered to `VERIFIED` in the
same call.

The rejected alternative is "load all claims, filter where status ==
VERIFIED". Functionally identical today; structurally worse forever. A filter
is a step that can be forgotten in a new call site, moved above a branch, or
subtly inverted. A single accessor that only ever returns approved content
cannot be any of those things.

**Providers are handed strings, never entities.** `CreativeBrief` carries
`list[str]`, not `list[ProductClaim]`. A provider cannot check a verification
status and must therefore never be in a position where it would need to.

The claim _ids_ are returned alongside the brief and stored on
`Script.sourced_claim_ids`. That is the audit trail: a claim withdrawn next
month can be traced to every script that leaned on it. Stored rather than
recomputed, because recomputing from current state would lose precisely the
scripts written while the claim was still approved — the ones that matter.

### 2. A product with nothing verified is refused, not served

`NoVerifiedContentError` (409, `CLAIM_NOT_VERIFIED`) rather than generating
from an empty brief. A video built from nothing confirmed is a video built from
the model's imagination, which is what §13 forbids. Its own error type so the
UI can say "verify some facts first" instead of "something went wrong" — the
difference between an instruction and an apology.

### 3. The project state machine is derived, not hand-listed

Eleven statuses would need roughly forty hand-written edges, and hand-written
edges disagree with each other. `_build_project_transitions` derives them from
three rules: forward one stage, back one stage (§103 rule 4), and into `FAILED`
and back out to the stage that failed (§105's 恢复后回原合理状态).

Two deliberate additions on top:

- `READY → STORYBOARDING`, so §103 rule 10's one-click regeneration lands
  somewhere.
- `DRAFT ↔ CREATIVE_PLANNING`, skipping `ANALYZING`. This is the one skippable
  stage and it has to be: a project's analysis stage means "analyse the product
  this project is for", and PHASE 6 made that a _product_ concern. Forcing an
  already-analysed product's projects through it would mean either a second
  billed vision call or a status set and immediately cleared.

The tests assert the _properties_ — a draft cannot fail, recovery does not
return to the beginning, archive is terminal — rather than restating the table,
because restating a derivation proves nothing about whether the derivation is
right.

### 4. Structured output is validated before it is stored (§107)

`CreativePlanSet` requires exactly three plans with distinct titles;
`ScriptDocument` requires §17's nine sections, in order, once each. §107 says a
badly formed response must not reach the database, and these are where that is
true.

The distinct-titles rule earns its place: a model asked for three variations
will cheerfully return three paraphrases of one idea, which technically
satisfies "three plans" while quietly removing the choice the product promises.

The real adapter re-prompts once on a validation failure (§107's "retry
parse"), sending the model its own error rather than the identical prompt —
re-sending unchanged input is superstition. A transport retry would be wrong
here: the call succeeded and was billed.

### 5. Word budget from duration, not from adjectives

§17 requires the script's length to be budgeted against the target duration.
`character_budget(seconds)` converts at a spoken-character rate and is passed to
the model as a number, because "make it about thirty seconds" produces wildly
varying lengths and a number does not.

The estimate is recomputed from the returned narration rather than trusted from
the model's own `duration_seconds` — a model's arithmetic is not evidence. A
script landing outside tolerance is **logged and shown, not rejected**: §17 asks
for a budget, and discarding work a human might prefer to trim is worse than
flagging it.

### 6. Versions are immutable; edits create new ones

Regenerating plans creates a new `version` and clears the previous selection —
a stale `selected` flag pointing at round one would silently drive the next
script. Scripts likewise: generation and human edits both append, and approving
supersedes the rest through a partial unique index, so PHASE 8's "which script?"
has exactly one answer.

## Consequences

**Good.** The §109 boundary is one function and one accessor, checkable by
reading. The audit trail survives a withdrawn claim. Both engines run on mocks
with no key, and the mock is honest enough to develop against — it says
"no approved claim available" in `risk_notes` rather than inventing one.

**Costs.** The `CreativeBrief`/string boundary means the ids must be carried
separately, which is slightly more plumbing than passing entities. That is the
price of making the safety property structural, and it is worth it.

Generation is synchronous, as PHASE 6's analysis is. §83's allowance covers it
for now; PHASE 9 moves both behind the queue.

**Unresolved.** `AnthropicLLMProvider` has never run against the live API — the
same gap as its vision sibling, for the same reason. Request construction, both
schemas, the parse-retry loop and every error branch are tested against a
stubbed client; whether the request shape is one the vendor accepts is not.

## Alternatives considered

| Option                                           | Why not                                                                                                                                     |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Filter claims at each call site                  | A filter is a step that can be forgotten. An accessor that only returns approved content cannot be.                                         |
| Pass `ProductClaim` objects to the provider      | A provider cannot check a verification status; handing it one invites a future adapter to read `.claim_text` off an unapproved row.         |
| Recompute `sourced_claim_ids` from current state | Loses the scripts written while a since-withdrawn claim was approved — the exact set somebody investigating would need.                     |
| Reject a script that misses its duration budget  | §17 asks for a budget, not a hard gate. Discarding work a human would rather trim is the worse failure.                                     |
| Hand-write the project transition table          | Eleven statuses, ~40 edges, and no way to tell a deliberate omission from a typo. Derivation makes the rules readable and the gaps visible. |
| Generate creative and script in one call         | §16 requires the user to _choose_ between three directions. A single call would make the choice cosmetic.                                   |
