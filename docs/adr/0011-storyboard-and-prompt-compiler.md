# 11. Storyboard, the prompt compiler, and product identity lock

Date: 2026-08-12
Status: Accepted
Phase: 8 (§18, §19, §29, §107)

## Context

§19 opens with a prohibition rather than a design:

> 严禁直接把用户一句自然语言送给视频模型。

Never hand a video model a sentence a user typed. The reason is not that users
write badly. It is that a video model reads a prompt as one undifferentiated
whole, so the parts that must not be negotiable — the product's shape, its logo
placement, the text printed on its packaging — carry no more weight than the
parts that should be. A free-text prompt makes product identity a suggestion.

§29 adds the other half: the platform must be able to lock a shot's product
identity to specific uploaded references, per shot rather than globally.

PHASE 8 is where both become code.

## Decision

### 1. Prompts are assembled, never written

`compile_shot_prompt` builds §19's thirteen named blocks in a fixed order from
structured fields. What varies between shots is the _content_ of the blocks;
what never varies is that the identity block exists and says the same thing.

**The enforcement is the absence of a field.** `UpdateShotRequest` has no
`visual_prompt` and no `negative_prompt`. A user edits lighting, camera,
composition; the prompt is recompiled from them on every change, so the two
cannot drift. There is no code path — and, more importantly, no API surface —
by which a typed sentence reaches a provider.

That absence only enforces anything because unknown request fields are now
**rejected**, which they were not (see §4 below).

Empty blocks are omitted rather than emitted as `LIGHTING:` followed by
nothing. A labelled empty section reads to a model as "lighting does not
matter here", which is the opposite of what an unset field means.

### 2. §19's consistency rules are quoted, not paraphrased

`IDENTITY_RULES` is the taskbook's own eight lines, verbatim, as a constant.
They are not templated per shot and not reworded per provider, because every
rewording is an opportunity to soften one — and "preserve logo placement"
softened into "keep the branding consistent" is exactly how a generated product
ends up with its logo on the wrong face.

### 3. The identity lock defaults by shot type (§29)

On for `PRODUCT_HERO`, `MACRO`, `ROTATION`, `MATERIAL`, `FEATURE`, `EXPLODED`
— the shots where the product fills the frame and a drifted shape is
unmissable. Off for `LIFESTYLE` and the rest.

This is not a safety compromise. The identity rules constrain _composition_ as
much as identity, and applying them to a wide room shot produces stiff
product-catalogue footage while protecting a product that occupies forty
pixels. The lock is a visible per-shot control, so the person looking at the
shot makes the call.

The identity _negatives_ ("different product", "altered logo") are conditional
on the same flag, and for the same reason: in a lifestyle frame that
deliberately includes other objects, "different product" fights the shot.

Reference images are resolved by role, best-first — a front-on photograph is a
better identity reference than a packaging shot, which shows a box. The model
names the _kind_ of reference a shot needs; the service picks which image,
because the model has never seen the asset table.

### 4. Unknown request fields are an error, project-wide

Pydantic ignores unrecognised fields by default. For a request body that is
wrong twice over: a client PATCHing `duration_secondss` gets a 200 and no
change, and believes it worked; and a read-only field becomes writable-looking
by accident.

This was discovered by a test that asserted §19's enforcement and _passed a
prompt through with a 200_. All seventeen request models now inherit
`ApiRequest`, which sets `extra="forbid"`.

One PHASE 5 test changed as a result: writing `status` through the product edit
endpoint used to be silently ignored and is now refused. The new behaviour is
better — silence taught callers the field was accepted.

### 5. §18's duration constraint is enforced twice

The shots must sum to the project's duration. Two mechanisms, doing different
jobs:

- `fit_shot_durations` **scales** the model's proportions onto the target. A
  model is good at "short hook, long reveal" and bad at making eight numbers
  sum to thirty, so scaling keeps the judgement and discards the arithmetic.
- `validate_storyboard_duration` **refuses** anything still outside 10%.

The tolerance is 10% against the script's 35%, and the difference is not
arbitrary. A script's word budget forecasts how long narration _will_ take; a
storyboard's total is what the renderer _will produce_. A forecast may be
loose; an instruction may not.

Scaling alone was not enough, and the bug is worth recording: scaling `[3,7,4]`
onto 30s wants `[6.4, 15, 8.6]`, the middle value clamps to §18's 10s ceiling,
and five seconds vanish — leaving a 25s storyboard that _looks_ fitted. The
remainder is now redistributed across shots with headroom, repeatedly.

When a target genuinely cannot be reached — five shots cannot fill sixty
seconds at ten each — the fitter gets as close as the bounds allow and the
validator rejects it. That is correct: the storyboard needs a different number
of shots, and silently returning a wrong total would hide that.

Approval re-validates, because shots can be edited after generation and
approval is the last moment before PHASE 9 starts spending money.

## Consequences

**Good.** §19's prohibition is enforced by API shape rather than by convention.
§29's rules are auditable as a constant. Every request body in the product now
rejects typos instead of swallowing them. The duration constraint holds through
generation _and_ editing.

**Costs.** The compiler must be re-run on every shot edit, so a shot's prompt
is never independently editable — a user who wants a specific phrase has to
express it through a field. That is the point, but it is a real constraint, and
PHASE 20's editor may need a supervised escape hatch.

`Storyboard.total_duration_seconds` is denormalised and recomputed after edits.
The alternative — aggregating on every read — would make §18's rule expensive
enough that somebody would skip checking it.

**Unresolved.** `AnthropicLLMProvider.generate_storyboard` has never run
against the live API, like its two siblings. The Protocol caught it missing
entirely during this phase, which is the argument for the Protocol.

## Alternatives considered

| Option                                         | Why not                                                                                                                                         |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Let users write the prompt, with guardrails    | §19 forbids it outright, and guardrails on free text are advisory. The absent field is not.                                                     |
| One global identity-lock setting per project   | Whether the product must match exactly depends on whether the shot is a macro or a wide room. A project-level switch is wrong for half of them. |
| Paraphrase the identity rules per shot         | Every rewording can soften one, and the softened ones are invisible until a video comes back wrong.                                             |
| Let the model pick reference asset ids         | It has never seen the asset table. It would produce references to nothing.                                                                      |
| Reject a storyboard whose durations do not sum | The shape is usually right and only the arithmetic wrong. Scaling rescues the common case; the validator still catches what scaling cannot.     |
| Aggregate the total on read instead of storing | Makes §18's check expensive enough to skip. Recomputing after edits is cheaper and the constraint stays free to verify.                         |
