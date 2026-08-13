# Monitoring and alerting

P23-T11, P23-T12. What to watch, what to page on, and — more usefully — what
not to.

The distinguishing question for every alert below is **what would the person
woken up actually do?** An alert with no answer is a dashboard panel that has
been given a pager, and the cost of those is that people stop reading the ones
that matter.

---

## 1. What the application already emits

No agent or instrumentation library is required to start. The application logs
structured JSON to stdout (§63) and every line in a request carries the same
`request_id`, which the nginx access log also records — so a proxy log and an
application log join on one field.

The event names below are stable and are what the alerts key on.

| Event                               | Emitted when                   | Fields worth alerting on                  |
| ----------------------------------- | ------------------------------ | ----------------------------------------- |
| `request completed`                 | every request                  | `http_status`, `duration_ms`, `http_path` |
| `job_created`                       | a job is queued                | `job_type`, `provider`, `queue`           |
| `job_transitioned`                  | any §106 state change          | `from`, `to`, `error_code`                |
| `job_failed`                        | a job reaches FAILED           | `error_code`, `kind`, `retries_used`      |
| `job_retry_scheduled`               | §24 schedules a retry          | `attempt`, `delay_seconds`                |
| `stuck_jobs_reaped`                 | the sweep found abandoned jobs | `reaped`, `requeued`                      |
| `circuit_opened` / `circuit_closed` | §55's breaker                  | `provider`, `open_seconds`                |
| `credits_insufficient`              | a reservation was refused      | `workspace_id`, `requested`               |
| `moderation_match`                  | §61's screen fired             | `decision`, `categories`                  |
| `ssrf_blocked`                      | §61 refused a URL              | `host`                                    |
| `audit`                             | any §60 action                 | `action`, `succeeded`                     |

Ship stdout to whatever aggregates logs on your platform. Do not write to files
inside a container.

---

## 2. The four alerts that should page

Each has a threshold, a reason for that threshold, and a first action.

### Job failure rate above 10% over 15 minutes

```
count(job_failed) / count(job_created) > 0.10  for 15m
```

**Why 15 minutes.** A single provider hiccup produces a burst that §24's retry
absorbs. Fifteen minutes of sustained failure is not a hiccup.

**First action.** Group `job_failed` by `error_code`. A wall of
`PROVIDER_UNAVAILABLE` or `PROVIDER_RATE_LIMITED` is theirs; a wall of
`PROVIDER_REJECTED` is ours — usually a prompt change that started tripping a
policy. Check whether `circuit_opened` fired; if it did, fallback is already
working and this may not need a human at all.

### Queue depth growing for 10 minutes

```
celery queue length increasing monotonically for 10m
```

**Why monotonic rather than a threshold.** A depth of 200 during a batch import
is normal and a depth of 20 that only grows is not. The shape is the signal.

**First action.** `celery inspect active`. Nothing active means the workers
cannot reach the broker; everything active means the arrival rate exceeds the
concurrency, and the fix is to scale _that queue_, not all of them.

### 5xx rate above 1% over 5 minutes

```
count(http_status >= 500) / count(request completed) > 0.01  for 5m
```

**First action.** Every 5xx response carries a `request_id`, and every log line
of that request carries the same one. Pull one and read the request end to end
before forming a theory.

### Stuck jobs reaped, at all

```
stuck_jobs_reaped where reaped > 0
```

**Why any occurrence.** The reaper existing is normal; it _finding_ something
means a worker died mid-job. One is a machine being replaced. A steady trickle
is workers being killed — check OOM on the render queue first, since that is
where the memory goes.

---

## 3. Worth a dashboard, not a page

- **Generation volume and cost.** Already served by `GET /api/v1/admin/analytics`
  (P22-T07/T08) and rendered by the operator console.
- **Per-provider success rate.** In the same response, and on the admin
  console's provider list.
- **Credit balances near zero.** A workspace that runs out gets a clear 402 and
  a message; nobody needs waking.
- **Moderation flags.** `moderation_match` with `decision=FLAGGED` is a review
  queue, not an incident.
- **p95 request latency.** Useful over weeks, misleading over minutes — the
  OpenAPI export and a batch import are both legitimately slow.

---

## 4. What is deliberately not alerted on

**Individual job failures.** §24 retries them. Paging on one means paging on
every transient provider blip.

**`ENABLE_CREDITS` refusals.** A workspace out of credits is a billing
conversation, not an outage.

**`ssrf_blocked`.** The defence worked. Worth a weekly review if the rate
changes, worth nothing at 3am.

**Readiness probe flapping.** `/ready` reports dependency reachability and is
_supposed_ to go red during a Redis failover. The orchestrator acts on it; a
human does not. This is also why the container healthcheck targets `/health`
and not `/ready` — a liveness probe wired to readiness restarts a healthy API
every time a dependency blinks (§70).

---

## 5. Adding metrics later

The application has no metrics endpoint today, and that is a defensible place
to stop: everything above is derivable from the log stream, and a Prometheus
endpoint is a second thing to secure and scrape.

When it becomes worth adding, the natural shape is a `/metrics` endpoint on the
API and a `worker_metrics` exporter alongside each queue, exposing:

- `aipvs_jobs_total{job_type,status,provider}` — a counter
- `aipvs_job_duration_seconds{job_type}` — a histogram
- `aipvs_queue_depth{queue}` — a gauge
- `aipvs_provider_failures_total{provider}` — a counter, and the input the
  circuit breaker already derives from the database

It must not be public. §61's headers apply to it like any other route, and
`/metrics` is a map of the system's internals.
