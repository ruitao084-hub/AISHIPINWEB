"""Job orchestration (§22-§24).

Owns the async contract: create, reserve, enqueue, return 202. Then lock,
submit, poll, ingest media, complete. Includes idempotency on
``(workspace_id, idempotency_key)`` (§23), retry classification (§24) and the
job state machine (§106).

Celery is transport, not truth — job state lives in Postgres, so a lost
message delays work without losing it (ADR-0004).

Populated from PHASE 9.
"""
