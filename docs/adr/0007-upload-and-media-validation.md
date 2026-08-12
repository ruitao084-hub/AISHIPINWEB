# ADR-0007: Direct-to-storage uploads with retroactive validation

- **Status:** Accepted
- **Date:** 2026-08-12
- **Phase:** 4 (P4-T02 … P4-T07)

## Context

Taskbook §12 specifies a presigned-URL upload flow and lists what must be
validated: MIME type, extension, file size, image pixels, video duration,
malicious files, and a file hash. §116 forbids large media from passing through
the API, and §11 requires the server to name every stored object.

Those two constraints pull against each other. If the API never sees the bytes,
it cannot validate them as they arrive.

## Decision

### The handshake

```
POST /uploads/presign  ->  browser PUTs to storage  ->  POST /uploads/{id}/complete
```

`presign` validates what is knowable without content — the declared MIME type
against a closed whitelist, the declared size against the per-type limit — then
reserves a server-generated key and writes a `MediaAsset` row in `PENDING`.
`complete` verifies the object that actually landed and promotes the row to
`READY`.

The `PENDING` row is written **before** the bytes exist. That ordering is what
makes the second phase safe: `complete` verifies a key the server issued and
persisted, not one the client hands back.

### Validation happens in `complete`, not during transfer

This is the consequence of §116, stated plainly: a malicious file exists in the
bucket between the PUT and the `complete` call. Three things contain it.

1. The bucket is private (§110); the only read path is a signed URL.
2. Nothing is served from a `PENDING` asset — the library query filters on
   `READY`.
3. A failed check deletes the object rather than leaving it.

`complete` runs, in order: `HEAD` for the real size, a **ranged read of the
first 64 bytes** for a magic-byte check against the declared type, then either
image decoding or an ffprobe pass.

### Content checks

- **Magic bytes.** A closed signature table. `image/svg+xml` is not on the
  whitelist at all — it is scriptable — and HTML declared as a JPEG fails the
  signature check. MP4 and QuickTime share one ISO-BMFF signature deliberately:
  real files routinely carry a brand that disagrees with the browser's guessed
  MIME type, and rejecting those would block valid footage without adding
  security.
- **Images** are decoded with Pillow from `Image.open`, which parses the header
  and stops. Dimensions are therefore read _before_ any pixel data is decoded,
  which is what makes the decompression-bomb ceiling
  (`max_upload_image_megapixels`, default 50) cheap enough to be a gate rather
  than a post-hoc check.
- **Video** is probed with ffprobe against a presigned download URL. ffprobe
  reads container headers over ranged HTTP, so a 500 MB upload is inspected in
  a few hundred kilobytes and the API never holds the file. The subprocess is
  built as a fixed argv (never a shell string), the source is refused if it
  could parse as a flag, and `-protocol_whitelist` is narrowed per call site —
  `file` for a local path, `http,https,tcp,tls` for a URL — so a crafted
  playlist cannot read the filesystem or reach the network.

### File hashing is complete for images and deferred for video

§12 asks for a file hash. Images are hashed with SHA-256 while their bytes are
already in memory for dimension probing, so it costs no extra transfer.

**Video is not hashed in PHASE 4.** Doing so would mean streaming the whole
object through the API process — exactly what the presigned flow exists to
avoid. The storage ETag is recorded in `metadata` as an opaque identifier (it
is a digest-of-digests for multipart uploads, so it is not a content hash and
is never presented as one), and SHA-256 for video moves to the ingest worker
when the job system lands in PHASE 9. An integration test asserts
`checksum is None` for video so the gap stays visible rather than being
mistaken for coverage.

### A rejected upload's `FAILED` status is written in its own transaction

Rejection is raised as an error, and the request-scoped session rolls back on
any exception. A `FAILED` written through that session is therefore undone on
the way out, leaving a `PENDING` row pointing at an object the rejection had
already deleted — a state worse than either outcome, because the client's retry
finds nothing to complete and the collector cannot tell an abandoned upload
from a rejected one.

The failure record is committed independently, the way an audit entry is: it
describes something that happened, and the request failing is precisely why it
must survive. This was found by a test, not by inspection.

### Idempotency

Completing an already-`READY` asset returns it unchanged rather than re-probing
or returning 409 (§67), so a retry after a dropped response is safe. A `FAILED`
asset cannot be completed again — the client presigns a fresh one, which is
what the uploader's retry does.

### Client

The browser PUTs with `XMLHttpRequest` rather than `fetch`, for one reason:
`fetch` still has no upload-progress event in any shipping browser, and a
progress bar that jumps 0% → 100% on a 200 MB video is not a progress bar.
Uploads run three at a time; twenty parallel PUTs divide the same bandwidth
into twenty simultaneously-stalling transfers.

Accepted MIME types and limits are fetched from `/uploads/config` rather than
hardcoded, so the picker's filter and the server's whitelist cannot drift.

## Consequences

- The API scales independently of upload bandwidth; a 500 MB video occupies no
  request worker.
- `complete` is a synchronous request that can run ffprobe. Bounded by
  `media_probe_timeout_seconds` (30s) and executed in a worker thread so it
  does not block the event loop, but it is still the slowest endpoint in the
  system and is a candidate to become a job in PHASE 9.
- Abandoned uploads leak `PENDING` rows and orphan objects until §163's
  collector exists. The leak is _recorded_ rather than invisible: a partial
  index on `(created_at) WHERE upload_status = 'PENDING'` is already in place
  for that collector to scan.
- Adding a format is one whitelist entry plus one signature. Adding a whole
  media class (audio, subtitles) additionally needs a probe path.

## Alternatives considered

- **Proxy uploads through the API.** Validation would be inline and the bucket
  would need no public write path at all. Rejected: §116 forbids it, and it
  makes upload bandwidth and API capacity the same resource.
- **Validate in a background job instead of in `complete`.** Better for very
  large video, and it is where video hashing is going. Rejected for PHASE 4
  because the job system does not exist until PHASE 9, and the acceptance
  criterion is a browser upload producing a usable `MediaAsset` — which needs a
  synchronous answer.
- **Trust the browser's `Content-Type`.** Simplest, and the reason stored-XSS
  via image upload is a recurring class of bug.
- **`python-magic` / libmagic for sniffing.** More formats, at the cost of a
  native dependency and a much larger surface than five signatures need.
