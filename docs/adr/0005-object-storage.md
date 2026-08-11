# ADR-0005: S3-compatible object storage, MinIO for local development

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 1 (P1-T07)

## Context

Taskbook §9 forbids binaries in Postgres and §4.6 requires storage abstracted as
S3-compatible so AWS S3, Cloudflare R2, MinIO, Aliyun OSS and Tencent COS are
all viable. §11 fixes the key layout, §12 requires browser-direct uploads, and
§110 requires private buckets with short-lived signed URLs.

The product is also aimed at Chinese e-commerce (§153), where Aliyun OSS and
Tencent COS matter commercially — so vendor portability is a product
requirement, not just engineering taste.

## Decision

One `ObjectStorage` Protocol, one `S3ObjectStorage` implementation driven by
`boto3` against a configurable endpoint. All five candidate providers speak the
S3 API, so provider choice is `S3_ENDPOINT` plus credentials, not code.

Specifics worth recording:

- **Path-style addressing** (`S3_FORCE_PATH_STYLE=true`) by default. MinIO and
  most self-hosted deployments cannot do virtual-host style without wildcard
  DNS. AWS deployments flip it off.
- **SigV4** everywhere, which every target supports.
- **Server-generated keys.** Filenames are UUIDs and the extension comes from a
  whitelist keyed on the validated MIME type. A user-supplied filename never
  reaches a key, which closes path traversal, null bytes and double extensions
  by construction rather than by sanitising (§11).
- **Synchronous client.** Every transfer-heavy caller (media ingestion,
  rendering) is a synchronous worker, and the only storage operation the async
  API performs — presigning — is local signing with no network I/O, so it
  cannot block the event loop. An async S3 library would add a dependency to
  solve a problem we do not have (§137).
- **Private buckets, always.** `minio-init` explicitly sets
  `mc anonymous set none`. Reads go through presigned GETs with a bounded TTL
  (60s–7d, enforced in settings).

## Consequences

- Switching provider is a configuration change. A future R2 or OSS migration
  copies objects and repoints `S3_ENDPOINT`.
- `boto3` leaks into no business code: services depend on the Protocol.
- Presigned upload URLs sign the `Content-Type`, so a client cannot sign for a
  JPEG then store an HTML document under that key — closing a stored-XSS route.
- Local development needs MinIO running. Where Docker Hub is unreachable,
  `moto` serves the same S3 API on the same port for the dev loop; CI always
  runs real MinIO so nothing merges having only been proven against a double.
- Multipart upload for very large files is not wired yet. Presigned single-PUT
  covers the §12 limits (20 MB images, 500 MB video); revisit if limits rise.

## Alternatives considered

- **Provider-native SDKs per cloud** (OSS SDK, COS SDK) — better access to
  vendor-specific features, but multiplies the code paths that must be tested
  and undoes the §4.6 abstraction. Both vendors offer S3-compatible endpoints.
- **Storing media in Postgres large objects** — explicitly forbidden by §9, and
  it would make backups and streaming pathological.
- **Proxying uploads through the API** — simpler to reason about, but §12 and
  §116 both require direct-to-storage transfer so a 500 MB upload never
  occupies an API worker.
