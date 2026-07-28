"""SSRF guard for server-side URL fetches (#18).

Any URL the model hands to `web.fetch` — or a redirect it lands on — is validated
here before we connect: **http/https only**, and the resolved IP(s) must be
public. Loopback, link-local (incl. the `169.254.169.254` cloud-metadata
address), private (RFC-1918 / IPv6 ULA), reserved, multicast, and unspecified are
all refused — on IPv4 and IPv6, and on **every redirect hop**. This closes the
injected-prompt → internal-network path (metadata creds, `localhost:8000`
operator API, private services).

Pure validation (`validate_url`) is resolver-injectable, so DNS-based SSRF —
a public hostname that resolves to `127.0.0.1` — is covered and testable offline.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

from core.logging import get_logger

log = get_logger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str], list[str]]


class SSRFError(Exception):
    """A URL or redirect target was refused as unsafe to fetch server-side."""


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Collapse IPv4-mapped IPv6 (::ffff:127.0.0.1) to v4 so the checks apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_ip(ip_str: str) -> None:
    """Raise SSRFError unless `ip_str` is a public address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError as e:
        raise SSRFError(f"not an IP address: {ip_str}") from e
    if _ip_blocked(ip):
        raise SSRFError(f"blocked (non-public) address: {ip_str}")


def _default_resolver(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def validate_url(url: str, *, resolver: Resolver = _default_resolver) -> str:
    """Raise SSRFError if `url` is unsafe to fetch; return the host on success.

    Checks the scheme, then every IP the host resolves to (a host that maps to
    ANY blocked address is refused — that's the DNS-rebinding / split-horizon case).
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise SSRFError("no host in URL")

    # IP literal → check directly, no DNS.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        check_ip(host)
        return host

    try:
        ips = resolver(host)
    except (OSError, socket.gaierror) as e:
        raise SSRFError(f"cannot resolve {host}: {e}") from e
    if not ips:
        raise SSRFError(f"no addresses for {host}")
    for ip in ips:
        check_ip(ip)
    return host


async def guarded_get(
    url: str,
    *,
    client: Any,
    max_redirects: int = MAX_REDIRECTS,
    resolver: Resolver = _default_resolver,
) -> Any:
    """GET `url`, following redirects MANUALLY and validating every hop.

    `client` must be an httpx.AsyncClient-like with redirects DISABLED
    (`follow_redirects=False`) — otherwise httpx would follow a redirect to an
    internal host before we could check it. Raises SSRFError on any unsafe hop or
    on a redirect loop.
    """
    current = url
    for _ in range(max_redirects + 1):
        validate_url(current, resolver=resolver)
        resp = await client.get(current)
        if resp.status_code in _REDIRECT_CODES:
            location = resp.headers.get("location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise SSRFError(f"too many redirects (> {max_redirects})")
