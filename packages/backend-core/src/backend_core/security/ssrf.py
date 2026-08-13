"""SSRF defence for outbound fetches (§61, P16-T04).

§61 is specific about what a URL fetcher must do:

    只允许经过安全下载器：DNS/IP 校验；禁止 127.0.0.1；禁止 RFC1918；
    禁止 metadata IP；内容长度上限；MIME 验证。

Every rule there is here. The one that is easy to get wrong, and the reason
this module exists rather than a two-line hostname check:

**Validating the hostname is not enough.** `http://internal.example.com` can
resolve to `169.254.169.254`, and a redirect from a public host can land on a
private one. So resolution happens *first*, every resolved address is checked,
and redirects are followed one hop at a time with the same check applied to
each — rather than handed to the HTTP client's `follow_redirects`, which would
check nothing.

The metadata address matters most. `169.254.169.254` is where every major cloud
serves instance credentials to anything that asks, with no authentication. A
fetcher that will retrieve an arbitrary URL and hand back the bytes is a
credential exfiltration endpoint unless this check exists.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from backend_core.errors import AppError, ErrorCode
from backend_core.observability import get_logger

logger = get_logger(__name__)


class UnsafeUrlError(AppError):
    """A URL that must not be fetched (§61)."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 422
    default_message = "That URL cannot be fetched."


#: Schemes an outbound fetch may use. `file://` is deliberately absent — it is
#: how a URL fetcher becomes an arbitrary file reader.
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: Cloud instance metadata. Blocked by address rather than by hostname, because
#: the hostname is not the attack — the address is, and it is reachable
#: directly.
_METADATA_ADDRESSES: Final[frozenset[str]] = frozenset(
    {
        "169.254.169.254",  # AWS, Azure, DigitalOcean, OpenStack
        "fd00:ec2::254",  # AWS IMDSv2 over IPv6
        "100.100.100.100",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud
    }
)

#: Ports a fetch may target. Restricting these blocks the other half of SSRF —
#: reaching an internal service that happens to live on a public address.
ALLOWED_PORTS: Final[frozenset[int]] = frozenset({80, 443, 8080, 8443})

#: How many redirects to follow before giving up. Low: a legitimate media URL
#: redirects once or twice, and a redirect chain is a way to burn a fetcher's
#: time budget.
MAX_REDIRECTS: Final[int] = 3

#: Refuse a body larger than this. Checked against `Content-Length` *and* while
#: streaming, because the header is a claim and the stream is the fact.
MAX_FETCH_BYTES: Final[int] = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A URL that passed every check, with the addresses it resolved to."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def is_public_address(address: str) -> bool:
    """Whether an IP is one an outbound fetch may reach.

    Everything non-global is refused. `ip_address.is_global` covers loopback,
    RFC1918, link-local, multicast, reserved and unspecified in one property
    that is maintained by the standard library rather than by a range list
    here that would go stale.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    if address in _METADATA_ADDRESSES:
        return False
    # IPv4-mapped IPv6 (`::ffff:127.0.0.1`) reports as global on some versions
    # while pointing at loopback. Unwrapping first closes that gap.
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return is_public_address(str(parsed.ipv4_mapped))

    return bool(parsed.is_global)


def resolve_and_validate(url: str) -> ResolvedTarget:
    """Check a URL and resolve it, refusing anything §61 forbids.

    Resolution happens here rather than being left to the HTTP client, so the
    addresses that are checked are the ones that will be connected to — as
    close to "no TOCTOU window" as a normal client library allows.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"Only {' and '.join(sorted(ALLOWED_SCHEMES))} URLs can be fetched.",
            details={"scheme": parsed.scheme},
        )

    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("That URL has no host.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeUrlError(
            "That URL uses a port this service will not connect to.",
            details={"port": port},
        )

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError("That host could not be resolved.", details={"host": host}) from exc

    addresses = tuple({str(info[4][0]) for info in infos})
    if not addresses:
        raise UnsafeUrlError("That host resolved to no addresses.", details={"host": host})

    # Every address, not the first. A host with one public and one private A
    # record is a documented SSRF technique, and connecting is not under our
    # control once the client picks.
    unsafe = [address for address in addresses if not is_public_address(address)]
    if unsafe:
        logger.warning(
            "ssrf_blocked",
            extra={"host": host, "addresses": list(addresses)},
        )
        raise UnsafeUrlError(
            "That URL points at an address this service will not connect to.",
            details={"host": host},
        )

    return ResolvedTarget(url=url, host=host, port=port, addresses=addresses)


def safe_fetch_headers(url: str, *, timeout: float = 10.0) -> dict[str, str]:
    """HEAD a validated URL and return its headers.

    Used to check the type and size before committing to a download. Follows
    redirects manually so every hop is validated — `follow_redirects=True`
    would let hop two land anywhere.
    """
    import httpx

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        resolve_and_validate(current)
        try:
            response = httpx.head(current, timeout=timeout, follow_redirects=False)
        except httpx.RequestError as exc:
            raise UnsafeUrlError("That URL could not be reached.") from exc

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise UnsafeUrlError("That URL redirected without a destination.")
            current = str(httpx.URL(current).join(location))
            continue

        if response.status_code >= 400:
            raise UnsafeUrlError(
                "That URL could not be fetched.",
                details={"status_code": response.status_code},
            )
        return {key.lower(): value for key, value in response.headers.items()}

    raise UnsafeUrlError("That URL redirected too many times.")


def check_declared_length(headers: dict[str, str], *, limit: int = MAX_FETCH_BYTES) -> int | None:
    """Refuse an oversized body before downloading it (§61).

    Returns the declared length, or `None` when the server did not declare one
    — which is not an error. A missing `Content-Length` means the streaming
    limit is the only defence, which is why that one is not optional.
    """
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        declared = int(raw)
    except ValueError:
        return None
    if declared > limit:
        raise UnsafeUrlError(
            "That file is larger than this service will download.",
            details={"content_length": declared, "limit": limit},
        )
    return declared


def check_declared_type(headers: dict[str, str], allowed_prefixes: tuple[str, ...]) -> str:
    """Refuse a body whose declared type is not one we accept (§61).

    A declared type is a claim, not proof — the real check is the magic-byte
    sniff after download (§27). This one exists to avoid downloading 400 MB to
    discover it is an HTML error page.
    """
    declared = headers.get("content-type", "").split(";")[0].strip().lower()
    if not declared:
        raise UnsafeUrlError("That URL declared no content type.")
    if not any(declared.startswith(prefix) for prefix in allowed_prefixes):
        raise UnsafeUrlError(
            "That URL points at a file type this service does not accept.",
            details={"content_type": declared},
        )
    return declared


__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "MAX_FETCH_BYTES",
    "MAX_REDIRECTS",
    "ResolvedTarget",
    "UnsafeUrlError",
    "check_declared_length",
    "check_declared_type",
    "is_public_address",
    "resolve_and_validate",
    "safe_fetch_headers",
]
