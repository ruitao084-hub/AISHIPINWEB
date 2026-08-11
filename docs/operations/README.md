# Operations

Runbooks for people on call. Written to be followed under pressure: exact
commands, no prose detours.

## Planned runbooks

| Runbook                | Phase | Covers                                                                              |
| ---------------------- | ----- | ----------------------------------------------------------------------------------- |
| `deployment.md`        | 23    | Rolling out web, API, worker and render worker                                      |
| `migrations.md`        | 23    | Applying and rolling back schema changes safely (§73, §169)                         |
| `rollback.md`          | 23    | Reverting a bad release                                                             |
| `backup-restore.md`    | 23    | Postgres backup, PITR, restore drill (§114)                                         |
| `disaster-recovery.md` | 23    | DB restore, Redis loss, storage outage, provider outage, worker/render crash (§115) |
| `monitoring.md`        | 23    | Metrics, dashboards, alert thresholds (§64)                                         |
| `job-recovery.md`      | 16    | Stuck job reaper, credit reconciliation, orphaned objects (§160–§163)               |

## Health endpoints (§70)

| Endpoint      | Meaning                                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET /health` | Liveness. Never touches backing services — a failure here means the process is broken, so restart it.      |
| `GET /ready`  | Readiness. Probes Postgres, Redis and S3; returns 503 if any is down, so traffic drains without a restart. |

Workers report a heartbeat rather than serving HTTP.
