# API Documentation

FastAPI generates the authoritative OpenAPI document at `/openapi.json`
(taskbook §5.2, §132). This directory holds the narrative documentation that a
schema cannot express: auth flows, the upload handshake, job lifecycles and
render behaviour.

| Interactive | URL                                |
| ----------- | ---------------------------------- |
| Swagger UI  | http://localhost:8000/docs         |
| ReDoc       | http://localhost:8000/redoc        |
| Raw schema  | http://localhost:8000/openapi.json |

## Conventions (§41)

- All business routes under `/api/v1`.
- JSON in, JSON out. UTC, ISO 8601 timestamps. UUID identifiers.
- Uniform error envelope:

```json
{
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "Project not found",
    "request_id": "..."
  }
}
```

- Error codes come from the fixed taxonomy in §65 — never ad-hoc strings.
- Long-running work returns `202 Accepted` with a `job_id`; it never blocks.

## Planned pages

| Page                                                                | Phase |
| ------------------------------------------------------------------- | ----- |
| `auth.md` — register, login, refresh, token handling                | 3     |
| `upload.md` — presign → direct-to-S3 → complete handshake           | 4     |
| `generation.md` — job creation, idempotency, polling, cancel, retry | 9     |
| `render.md` — timeline build, render profiles, export, download     | 13    |
