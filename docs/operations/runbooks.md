# Runbooks

P23-T08, P23-T09, P23-T10. Migration, rollback and backup — written to be
followed at 3am by someone who did not write the code.

Every command here has been run against a real database during development
except where marked **not yet exercised**, which means exactly that: the
procedure is correct as written and nobody has performed it under load.

---

## 1. Deploying a new version (P23-T09)

### The order, and why it is this order

```
1. build and push images
2. run migrations to completion          ← separate step, not a start hook
3. roll the API
4. roll the workers
5. roll the web
```

**Migrations run as their own step.** Two API replicas starting together would
both attempt `alembic upgrade head`; the loser crashes on the advisory lock and
the orchestrator restarts it into the same race. `docker-compose.prod.yml`
expresses this as `depends_on: migrate: service_completed_successfully`.

**The API rolls before the workers.** A worker running new code against an API
that has not yet been updated is fine — they share a database, not an
interface. The reverse is also fine. The order matters only for the one case
below.

### The compatibility rule

**A migration must be safe against the version of the code currently running.**
That is the whole discipline, and it has one practical consequence: a column
being removed takes two deploys.

|                          | Deploy 1                          | Deploy 2               |
| ------------------------ | --------------------------------- | ---------------------- |
| Adding a column          | add it nullable, code ignores it  | code starts writing it |
| Adding a NOT NULL column | add nullable + backfill + default | set NOT NULL           |
| Removing a column        | code stops using it               | drop it                |
| Renaming a column        | add new, write both, backfill     | read new, drop old     |

Dropping a column in the same deploy that stops using it will work, right up
until a rollback puts the old code back in front of the new schema.

### Running it

```bash
# What is about to happen. Read it.
docker compose -f docker-compose.prod.yml run --rm migrate alembic history --indicate-current

# The SQL, without applying it. For anything touching a large table, read this
# too — an unexpected table rewrite is visible here and nowhere else.
docker compose -f docker-compose.prod.yml run --rm migrate \
  alembic upgrade head --sql

# Apply.
docker compose -f docker-compose.prod.yml run --rm migrate alembic upgrade head

# Confirm.
docker compose -f docker-compose.prod.yml run --rm migrate alembic current
```

### If a migration fails halfway

Postgres runs DDL transactionally, and Alembic wraps each revision in one — so
a failed revision is rolled back whole, and the database is at the previous
revision. Verify rather than assume:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate alembic current
```

Then fix the migration and redeploy. **Do not** run `alembic stamp` to make the
error go away: that tells Alembic a migration ran when it did not, and every
subsequent deploy inherits the lie.

---

## 2. Rolling back (P23-T10)

### First: does the schema need to move?

Most rollbacks do not. If the new version only added a nullable column, the old
code ignores it and reverting the images is the whole procedure.

```bash
IMAGE_TAG=<previous-tag> docker compose -f docker-compose.prod.yml up -d \
  api web worker-video worker-tts worker-render worker-qc worker-default beat
```

### If the schema does need to move

```bash
# One revision at a time, checking between.
docker compose -f docker-compose.prod.yml run --rm migrate alembic downgrade -1
docker compose -f docker-compose.prod.yml run --rm migrate alembic current
```

Every migration in this repository has been round-tripped
`upgrade → downgrade → upgrade` against a real database during development.
That is what makes this a procedure rather than a hope — but a downgrade that
was safe on an empty schema can still lose data on a full one.

**A downgrade that drops a column deletes the data in it.** There is no
recovery from that except the backup. Before any destructive downgrade:

```bash
pg_dump "$DATABASE_URL" --format=custom --file=pre-rollback-$(date +%s).dump
```

### What a rollback cannot undo

- **Object storage.** Renders and uploads written by the new version stay. They
  are addressed by key and the old code will simply not know about them; the
  orphan collector reclaims them (§163).
- **Credits already captured.** The ledger is append-only by design (§95). A
  charge is reversed with a compensating `ADJUSTMENT`, never by rolling back —
  an ledger you can rewind answers "what is the balance" and not "how did it
  get there".
- **Jobs in flight.** Workers are replaced mid-task. §22's locking and
  `task_acks_late` make the redelivery safe, so those jobs re-run rather than
  vanish; a render that was 90% done starts again.

---

## 3. Backups (P23-T08)

### What has to be backed up

|                | Backed up by                                 | Recovery                   |
| -------------- | -------------------------------------------- | -------------------------- |
| Postgres       | managed snapshots + PITR                     | restore to a point in time |
| Object storage | bucket versioning + cross-region replication | restore object versions    |
| Redis          | **nothing, deliberately**                    | see below                  |
| Secrets        | the secret manager's own versioning          | restore a version          |

**Redis is not backed up on purpose.** It holds queued tasks, rate-limit
counters and job locks — all reconstructible. Losing it costs the in-flight
queue, which the stuck-job reaper recovers within its sweep interval (§161).
Backing it up would create a second source of truth for job state that could
disagree with the database, which is worse than losing it.

### Verifying a backup

An untested backup is a belief. Quarterly, restore into a scratch database and
check that the schema is at head and the row counts are plausible:

```bash
createdb aipvs_restore_test
pg_restore --dbname=aipvs_restore_test --no-owner latest.dump

DATABASE_URL="postgresql+psycopg://.../aipvs_restore_test" \
  uv run alembic current   # must print the expected revision

psql aipvs_restore_test -c "
  SELECT 'workspaces' t, count(*) FROM workspaces
  UNION ALL SELECT 'products', count(*) FROM products
  UNION ALL SELECT 'generation_jobs', count(*) FROM generation_jobs
  UNION ALL SELECT 'credit_transactions', count(*) FROM credit_transactions;"

dropdb aipvs_restore_test
```

**Not yet exercised in production.** The commands are correct; nobody has run
this against a real snapshot.

### Restoring

1. Stop the workers first. A worker writing into a database being restored
   produces a state that matches neither the backup nor the present.
   ```bash
   docker compose -f docker-compose.prod.yml stop worker-video worker-tts \
     worker-render worker-qc worker-default beat
   ```
2. Restore through the managed service's console or `pg_restore`.
3. Check the revision matches the running images:
   `alembic current` against the deployed tag's expectations.
4. Start the API, then the workers.
5. Sweep for jobs stranded by the outage — they will be in `PROCESSING` with no
   worker behind them, and the reaper handles them, but not before its
   threshold elapses:
   ```bash
   docker compose -f docker-compose.prod.yml run --rm worker-default \
     celery -A aipvs_worker.celery_app call aipvs.maintenance.reap_stuck_jobs
   ```

---

## 4. Common incidents

### Queue depth climbing

```bash
docker compose -f docker-compose.prod.yml exec worker-video \
  celery -A aipvs_worker.celery_app inspect active_queues

docker compose -f docker-compose.prod.yml exec worker-video \
  celery -A aipvs_worker.celery_app inspect active
```

If nothing is active, the workers are not consuming — check they can reach the
broker. If everything is active, the arrival rate exceeds the concurrency:
scale the specific queue's replicas, not all of them.

### A provider is failing

The circuit breaker opens on its own after five consecutive failures and
traffic falls back (§55). If it has not, or you want it out now:

```bash
curl -X POST https://<host>/api/v1/admin/providers/<name>/enabled \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false, "notes": "INC-123"}'
```

Re-enabling clears the breaker, which is what "try this again now" means.

### Jobs stuck in PROCESSING

The reaper sweeps every five minutes. To force one:

```bash
docker compose -f docker-compose.prod.yml run --rm worker-default \
  celery -A aipvs_worker.celery_app call aipvs.maintenance.reap_stuck_jobs
```

It goes through `JobService.fail`, so credits are released and §24's retry
policy applies. A hand-written `UPDATE ... SET status='FAILED'` would skip
both, leaving the reservations held.

### Disk filling on a render worker

Renders work in a temporary directory that is removed on the way out (§35). A
worker killed mid-render leaks one. They are bounded by the container's
filesystem, so the fix is to replace the container:

```bash
docker compose -f docker-compose.prod.yml restart worker-render
```

If this recurs, the renders are being killed — check for OOM before assuming a
cleanup bug.
