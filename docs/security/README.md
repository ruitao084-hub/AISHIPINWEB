# Security

Baseline requirements from taskbook §61, plus the rules specific to this system.

## Non-negotiable rules

| Area             | Rule                                                                                                                                                                               |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Secrets          | Server-side environment variables only. Never in the browser, never in git, never in logs (§0.1 rules 9–10, §63).                                                                  |
| FFmpeg           | Argument arrays only. Never `shell=True`, never string interpolation of user input. Server-generated paths, isolated temp dir, timeout, cleanup in `finally` (§35, §117).          |
| Uploads          | Validate MIME, extension, size, dimensions and duration. Server-generated UUID filenames — a user-supplied filename is never trusted (§11, §12).                                   |
| SSRF             | Any fetch of a user-supplied URL goes through the safe downloader: DNS/IP validation, block loopback, RFC1918 and cloud metadata addresses, cap content length, verify MIME (§61). |
| Prompt injection | Product text, OCR output and uploaded documents are data, never instructions. Templates say so explicitly (§38, §108).                                                             |
| Authorisation    | Enforced server-side on every workspace-scoped resource. Hiding a button is not access control (§40).                                                                              |
| Storage          | Private buckets. Downloads use short-lived signed URLs (§110).                                                                                                                     |
| Logging          | Never log API keys, passwords, full `Authorization` headers or raw image base64. Provider errors are redacted before storage (§63, §166).                                          |

## Planned documents

| Document                                                 | Phase   |
| -------------------------------------------------------- | ------- |
| `secrets.md` — handling, rotation, secret manager        | 16 / 23 |
| `threat-model.md`                                        | 16      |
| `upload-validation.md`                                   | 4       |
| `ffmpeg.md` — safe invocation rules and review checklist | 13      |
| `ssrf.md` — the safe downloader contract                 | 16      |
| `rbac.md` — role/permission matrix                       | 3       |

## Reporting

Do not open a public issue for a vulnerability. See the repository security
policy once published (PHASE 23).
