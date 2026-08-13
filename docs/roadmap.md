# Post-MVP roadmap

PHASE 24 (§101). Seventeen items, and unlike every phase before it §101 gives
no acceptance criteria — it is a list of directions, not a specification.

So this document does what a list cannot: for each item it says **what it
actually is**, **what has to be true before it is worth building**, and — where
the answer is interesting — **why it was not built already**. Two of the
seventeen turned out to be cheap enough to finish now, given what PHASES 14 and
19 already put in place; those are marked **done** and the rest are honest
about their cost.

The ordering below is by what unblocks the most, not by §101's order.

---

## Done in this phase

### Automated retry via QC

PHASE 14 already runs §37's checks on every render; PHASE 20 already re-renders
from a stored timeline. The missing piece was one decision: **which QC failures
a re-encode could plausibly fix.**

`RERENDERABLE_CHECKS` in `jobs/qc_runner.py` answers it narrowly — duration,
resolution and container. A truncated output often is the encode. A video of
the wrong product is not, and retrying it would spend money to produce the same
wrong video, so identity and black-frame failures still need a person.

Bounded to one attempt, counted from prior failed `QualityCheck` rows rather
than an in-memory flag: a counter would not survive the worker, and a flag on
the render would not see the previous one.

### Provider quality score

PHASE 19's router already scores on failure rate, cost, latency and priority.
§101 asks for a quality score, and the honest source is §37's checks — they run
on every render and already grade PASSED / WARNING / FAILED. Inventing a
separate rating would mean a second opinion nobody had calibrated.

`ProviderRouter.quality_score` computes it over 30 days, weighting a warning at
0.6 rather than 0 — a warning is a video someone can ship with a note, and
averaging it with failures would make a provider that warns often look as bad
as one that fails often.

**It is reported, not routed on.** A render mixes several shots, so QC judges
the finished video and a failure implicates whichever provider made that
project's shots — approximate, and not a basis for excluding a provider. It
appears on the admin console; making it a routing term needs per-shot QC first,
which is the next paragraph.

---

## Worth doing next

### Per-shot visual QC

The prerequisite for several other items, including making the quality score
routable. §37.2 already describes visual QC — asking a vision model whether the
product on screen is the customer's product — and `ENABLE_QC` already gates it,
but it runs on the finished render.

Running it per _take_, before the timeline is assembled, would let a bad shot be
regenerated for the price of one shot rather than one video, and would attribute
quality to the provider that actually produced the frame.

**Cost:** one vision call per shot. At eight shots a video that is meaningful,
which is why it is a flag and not a default.

### SSE for job progress

§26 chose polling for the MVP and warned against reaching for WebSockets first,
which was right: `useJobPoll` is eighty lines and works. SSE is the natural next
step rather than WebSockets — job progress is one-directional, and a bidirectional
protocol buys nothing but a second connection lifecycle to get wrong.

**What has to be true first:** the proxy must not buffer (`X-Accel-Buffering: no`
in the nginx config, currently absent), and the API needs a connection budget —
an SSE stream holds a worker for its lifetime, and 500 idle streams on a
4-worker uvicorn is an outage.

### Webhooks

Customers with their own pipelines want to be told when a video is ready rather
than polling for it. The job state machine (§106) already emits every transition
worth sending.

**The hard part is not sending them.** It is retries with backoff, a dead-letter
after N failures, signing the payload so a receiver can verify it, and _not_
letting a customer's slow endpoint become our queue depth. That is a small
service, not an endpoint.

---

## Larger, and clearly scoped

### Public API

The OpenAPI document already exists and is generated from the same source the
web client uses (§5.2), so the contract is not the work. What is missing is API
keys as a first-class credential — distinct from a user's JWT, scoped to a
workspace, revocable, and rate-limited on their own budget rather than sharing
§123's per-workspace limits with the UI.

### Enterprise SSO

SAML or OIDC against the customer's IdP, with just-in-time provisioning into a
workspace. §38's permission model already separates roles from users cleanly, so
the mapping is `IdP group → WorkspaceRole` and not a redesign.

**The reason it is not built:** it is a sales-driven feature. Building it before
a customer names their IdP means building it against a guess, and every SSO
integration is different in exactly the details that matter.

### Team collaboration

Comments on shots, review assignment, an approval workflow beyond the current
single-actor verify. §60's audit trail already records who did what, so the
history exists; what does not is any notion of _asking_ someone to look.

### Content library

Reusable BGM, stock footage and brand assets across projects. `AudioTrack`
already carries §32's licence fields, which is the part that would be painful to
retrofit — a library of tracks whose provenance nobody recorded is a legal
problem, not a feature.

---

## Infrastructure, when scale demands it

### CDN for delivery

Renders are served by presigned URL straight from storage (§60), which is
correct and gets slow across an ocean. A CDN in front with signed URLs is
straightforward; the reason to wait is that it adds a cache-invalidation
question to every render, and today the answer is "there is no cache".

### Render autoscaling

The render queue is the expensive one and its depth is already the signal
(P23-T11). Scaling on queue depth needs the metrics endpoint that
`docs/operations/monitoring.md` §5 sketches — the logs carry the information
today but a horizontal pod autoscaler cannot read logs.

### GPU self-hosted models

Running a video model on owned hardware, once volume makes per-second provider
pricing worse than amortised GPU cost. §20's provider adapter is what makes
this a new adapter rather than a rewrite; §140's capability matrix is what stops
the router assuming the self-hosted model behaves like the hosted one.

### Temporal for orchestration

§22's job system is Celery plus a state machine plus explicit idempotency, and
it works. Temporal would replace the state machine with durable execution and
remove the hand-written retry ledger.

**Deliberately not now.** The current system's failure modes are understood and
its recovery paths — the stuck-job reaper, §24's taxonomy, the idempotency
constraints — have been built and verified. Replacing a working orchestrator is
a migration with no user-visible outcome.

---

## Judgement calls, not just work

### Smart reframe

Reframing a 16:9 render to 9:16 by tracking the subject rather than centre-
cropping. Genuinely valuable — most customers want both — and genuinely hard to
do without cutting the product out of its own advert.

**§29's identity lock makes this sharper than usual.** A crop that loses the
logo has produced a video of a different product, and the QC check for that
already exists (§37.2). So the honest version of this feature is: reframe, then
run visual QC on the result, and refuse rather than ship a crop that lost the
product.

### Prompt A/B testing

§15's registry already versions prompts immutably and every job records the key
and version it used — so the _data_ to compare two prompt versions is already
being collected. What is missing is an assignment mechanism and an honest
measure of "better".

**The measure is the problem, not the plumbing.** Generation quality has no
automatic ground truth; the available signals are QC status, whether a user
regenerated the shot, and whether they downloaded the video. Those are weak, and
an A/B framework that optimised a weak proxy would confidently make the product
worse.

### E-commerce connectors

Importing products from Shopify, Taobao or Amazon. PHASE 21's batch import
already handles the shape of this — rows to products to projects — so a
connector is a source adapter over the same pipeline.

**§13 is the constraint that makes this interesting.** A product description
scraped from a listing is a _claim someone else wrote_, and the Truth Layer's
whole argument is that unverified claims must not reach a script. So an imported
description arrives as `AI_INFERRED`, needing the same human confirmation as
anything the vision model produced — which is more friction than a connector
usually implies, and is the right amount.

### Advanced editor

Multi-track compositing, keyframes, transitions beyond §33's four types. PHASE
20's editor covers reorder, trim, gains, subtitle style and logo, which is the
set that does not require a timeline UI with a scrubber.

Everything past that needs a real editing surface, and a real editing surface
is a product decision — this platform's premise is that the AI does the editing
and the human approves it. An editor good enough to compete with a video editor
is an argument that the premise is wrong.
