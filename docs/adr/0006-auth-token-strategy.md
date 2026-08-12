# ADR-0006: Auth token strategy — short JWT access, rotating cookie refresh

- **Status:** Accepted
- **Date:** 2026-08-11
- **Phase:** 3 (P3-T05)

## Context

Taskbook §39 requires email/password auth with an access token and a refresh
token, Argon2 or bcrypt hashing, a password policy, login rate limiting, and
that the refresh token be HttpOnly, Secure and SameSite. §75 requires this
decision be recorded.

The constraint that shapes everything: the web app is a browser SPA, so any
credential it can read, injected JavaScript can read too.

## Decision

### Passwords: Argon2id

Chosen over bcrypt because it is memory-hard — resisting GPU and ASIC attack
rather than only raising iteration count — and because **bcrypt silently
truncates at 72 bytes**, quietly weakening a long passphrase. Parameters follow
OWASP's second Argon2id profile: 19 MiB, t=2, p=1.

Policy is length-only: minimum 12, maximum 256. Composition rules are
deliberately _not_ enforced — NIST SP 800-63B and OWASP both advise against
them, since they push people toward `Password1!` while blocking strong
passphrases. The maximum exists because hashing an unbounded string on an
unauthenticated endpoint is a cheap denial of service.

Passwords are NFKC-normalised before hashing, so a password set on one platform
still verifies on another.

### Access token: short-lived JWT, in memory, Authorization header

15 minutes by default, verified by signature so the hot path needs no database
round trip — except that `get_authenticated_user` _does_ reload the user, so a
suspended account loses access immediately rather than at token expiry. That is
a deliberate cost: correctness of revocation over saving one indexed lookup.

Held in a JavaScript variable, never `localStorage`. Storage persists across
tabs and reloads, so a token there can be harvested at leisure by any injected
script; a module variable requires the attacker to run while the tab is open.

### Refresh token: JWT with a `jti`, HttpOnly cookie, rotated on use

- **HttpOnly** so JavaScript cannot read the long-lived credential at all —
  the single most valuable thing an XSS bug could steal.
- **SameSite=lax** blocks cross-site POSTs while still sending the cookie on
  top-level navigation, which a returning user needs.
- **Secure** in production only; local development runs over http.
- **Path-scoped** to `/api/v1/auth`, so it is not attached to ordinary API
  calls that have no use for it.
- **Rotated on every refresh**, with the presented token revoked. A stolen
  refresh token is therefore usable at most once, and the legitimate user's
  next refresh fails — a detectable signal rather than a silent compromise.

The `jti` is what makes revocation possible for an otherwise stateless token:
logout writes it to a Redis denylist keyed to expire with the token, so the
list stays bounded by the refresh TTL instead of growing forever.

Both token types carry a verified `type` claim, so a refresh token cannot be
presented as an access token to turn a 15-minute credential into a 30-day one.

### Rate limiting

Login is limited per IP (10 / 5 min) **and** per account (5 / 15 min); the
second exists because distributing an attack across IPs would otherwise evade
the first. Registration is limited per IP to slow bulk account creation. A
successful login clears the counter so a user who mistypes twice is not locked
out by their own mistake.

The limiter **fails open** when Redis is unavailable. Stated plainly because it
is a real tradeoff: during a Redis outage, brute-force protection degrades to
whatever the edge provides. The alternative — failing closed — turns a cache
outage into a total login outage, which is worse.

## Consequences

- A cold page load has no access token and must exchange the refresh cookie
  first. The client handles this, and the protected layout waits on a `loading`
  state rather than redirecting a user who is in fact signed in.
- Concurrent 401s share a single in-flight refresh. Without that, five parallel
  requests would fire five rotations and four would be rejected.
- Cross-origin deployment requires CORS with credentials and an exact origin
  allowlist — a wildcard is rejected at boot in production (P2-T01).
- Revoking _access_ tokens is not possible before expiry; the 15-minute window
  is the exposure. Shortening it costs refresh traffic.

## Alternatives considered

- **Refresh token in the response body / `localStorage`** — simpler for a
  cross-domain client, and precisely what §39's HttpOnly requirement rules out.
- **Opaque server-side sessions** — trivially revocable and genuinely simpler,
  but every API call becomes a session lookup, and the workers and render
  workers would need the same session store. Revisit if token complexity grows.
- **bcrypt** — fine, widely deployed, but the 72-byte truncation is a real
  footgun and Argon2id is the current recommendation.
- **Access tokens with no database check** — faster, but a suspended user would
  keep full access until their token expired.
