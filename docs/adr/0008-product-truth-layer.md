# ADR-0008: The Product Truth Layer

- **Status:** Accepted
- **Date:** 2026-08-12
- **Phase:** 5 (P5-T04 … P5-T07)

## Context

Taskbook §13 sets the hardest constraint in the project: the platform must not
fabricate product parameters. Its worked example is an air purifier, where a
language model will confidently write

> 除甲醛率 99.9%

— a number it invented, about a real product, that a real company would then
broadcast. What it may write is

> 帮助过滤空气中的杂质和异味

and **only if that function has been confirmed as a fact**.

§109 adds the mechanism: before generating a script, call
`get_verified_claims(product_id)`, and never use `possible_selling_points` as
though it were fact.

The difficulty is that this cannot be enforced at the point of generation. By
the time a script is being written it is far too late to ask whether a number
is real. The guarantee has to be structural.

## Decision

### Two tables, four states, one accessor

`product_facts` carries a `VerificationStatus` of `AI_INFERRED`,
`USER_PROVIDED`, `VERIFIED` or `REJECTED`. `product_claims` carries
`SUGGESTED`, `VERIFIED` or `REJECTED` and cites the facts that support it.
Generation code calls `get_verified_claims` / `get_verified_facts`, which
return only `VERIFIED` rows — there is no filtering step for a caller to
forget, which is the difference between a rule and a convention.

### Three rules, all enforced server-side

**1. Anything the AI produces starts at `AI_INFERRED`.** `create_fact` derives
the status from the _source_ and overrides whatever the caller asked for. A
caller cannot insert a pre-verified AI fact even deliberately. PHASE 6's
analyser will call this same method, which is why the override lives in the
service rather than in the endpoint.

**2. Promotion requires a person**, recorded as `verified_by_user_id` and
`verified_at`. A database CHECK refuses a `VERIFIED` row with no timestamp —
so a partially-written verification cannot exist even if some future code path
forgets a field. That constraint caught a real bug on its first run: the
service was inserting a `VERIFIED` row and stamping the timestamp immediately
afterwards, in a second statement.

**3. A claim asserting anything checkable needs verified evidence.**
`FUNCTIONAL`, `PERFORMANCE`, `COMPARATIVE`, `CERTIFICATION` and `SAFETY`
claims cannot be verified without at least one `VERIFIED` cited fact, and the
cited facts must belong to the same product. `EMOTIONAL` is the sole exemption:
"brings a little calm to your morning" asserts nothing that could be
substantiated, so demanding evidence for it would be theatre.

Note that `FUNCTIONAL` is _not_ exempt. §13's own permitted example — "helps
filter impurities" — is allowed only if that function has been confirmed, so a
capability claim is an evidential claim.

### Withdrawal cascades

This is the rule that decays silently if nobody implements it.

Verify a fact. Verify a claim citing it. Now reject the fact. In a naive
design the claim stays `VERIFIED`, and a script goes on quoting evidence that
has been withdrawn — which is precisely the fabricated statement §13 forbids,
arrived at one individually-legitimate step at a time.

So rejecting a fact demotes every `VERIFIED` claim citing it back to
`SUGGESTED`. Editing a fact's **value** does the same, and additionally strips
the fact's own verification: a claim approved against "removes 99.9%" was not
approved for "removes 50%". Editing only the key or type leaves verification
intact, because the assertion has not changed.

### Vocabulary the taskbook did not fix

§10.7 and §10.8 name `fact_type`, `claim_type` and `risk_level` without
enumerating them. The values chosen here are organised around _what would have
to be true for the statement to be honest_, because that is what decides
whether evidence is required:

- `FactType` separates `PERFORMANCE` (a quantified outcome — §13's dangerous
  category) from `SPEC`, `MATERIAL`, `FEATURE`, `CERTIFICATION` and the rest,
  so the risky class is identifiable by type.
- `ClaimType` splits by assertion kind, as above.
- `ClaimRiskLevel` defaults `PERFORMANCE`, `COMPARATIVE`, `SAFETY` and
  `CERTIFICATION` to `HIGH` — these are the claims that attract regulatory
  attention — so a review queue can be ordered by consequence.

`FactSourceType` is deliberately distinct from `VerificationStatus`: a fact can
be `AI_VISION` in origin and `VERIFIED` in status, which is exactly the review
workflow §13 describes. Origin is never overwritten, so "a human confirmed
what the AI guessed" stays distinguishable from "a human typed it in".

### Product status

§104's machine is enforced by a transition table, not by assignment (§105).
`DRAFT → READY` is not a legal edge; `READY` is reachable only from
`ASSETS_READY` (a product whose facts were all hand-entered) or
`REVIEW_REQUIRED`. `mark_reviewed` additionally refuses a product with zero
verified facts — §13's "not enough verified claims" case, where the product has
nothing a script could truthfully say.

## Consequences

- Generating a video for a product with no verified facts produces visual
  ideas and no factual claims. That is the intended behaviour, not a gap.
- Verification is human work, and the product will feel it. There is no
  automatic path from an AI inference to a usable claim, by design.
- The cascade means a single fact rejection can quietly un-approve several
  claims. It is logged with a count for exactly that reason.
- `source_fact_ids` is JSONB rather than a join table, per §10.8. Containment
  (`@>`) queries it, which is fine at one product's scale; a join table would
  be needed if claims ever had to be searched by evidence across a workspace.
- `brand_kit_id` (§10.5) is **not** implemented yet. The `brand_kits` table
  arrives in PHASE 17, and a nullable UUID with no foreign key would be an
  unconstrained column pretending to be a reference. It is added with its
  table.

## Alternatives considered

- **A single `is_verified` boolean.** Loses the distinction between "the AI
  guessed this", "a user typed it", and "someone checked it" — and that
  distinction is the entire review workflow.
- **Verifying claims without checking their facts.** Simpler, and makes the
  citation decorative. A claim could then cite an unverified or rejected fact
  and still be approved.
- **Deleting rejected facts.** Loses the record, and lets the analyser
  re-propose something a human already refused.
- **Enforcing the rule at generation time only.** Rejected as the central
  mistake: by then the fabricated number already exists in the database and any
  code path that skips the check will emit it.
