"""Bearer-token auth middleware.

Compares `Authorization: Bearer <token>` against `VAULT_API_KEY`. Constant-time
compare. Skips a configurable allow-list of paths so health probes / the root
status page stay open.

**Fail-closed.** When no token is configured the server runs in "open mode" —
but open mode is restricted to *local (loopback)* callers. A public deployment
with no key set therefore cannot reach the data / code-running endpoints; you
must set `VAULT_API_KEY` to serve remotely. (Previously an empty key disabled
auth for everyone, which on a public URL is an unauthenticated RCE surface, since
`/v1/agents/*` runs LLM-authored shell commands.)

Intentionally simple — this is a personal-AI deployment with one user. If you
need scopes, multiple users, or token rotation, swap in OAuth2 / JWT here.
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from core.logging import get_logger

log = get_logger(__name__)


def _peer_is_local(request: Request) -> bool:
    """True if the request's TCP peer is loopback (i.e. local dev).

    A real ASGI server reports the peer as an IP address, so a routable/remote
    client is a parseable non-loopback IP → treated as remote. Behind a reverse
    proxy the peer is the proxy's (non-loopback) address → also remote, which is
    the safe default: set a token to serve publicly. The only non-IP peer in
    practice is the in-process ASGI test transport, which is never remotely
    reachable → treated as local.
    """
    client = request.client
    if client is None:
        return False  # no peer info → don't assume local
    try:
        addr = ipaddress.ip_address(client.host)
    except ValueError:
        return True  # non-IP peer = in-process transport (tests), not a network client
    # Dual-stack servers report IPv4 peers as ::ffff:a.b.c.d — unwrap before testing.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allow_paths: Iterable[str] = (),
        allow_prefixes: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self._token = token or ""
        self._allow_paths = tuple(allow_paths)
        # Prefix matches for things like the static UI bundle (/static/...),
        # which must load before the user has supplied the API key.
        self._allow_prefixes = tuple(allow_prefixes)
        if not self._token:
            log.warning(
                "auth.open_mode_local_only",
                detail="no VAULT_API_KEY set — serving loopback callers only; remote "
                "requests are refused. Set VAULT_API_KEY to serve publicly.",
            )

    def _is_open_path(self, request: Request) -> bool:
        path = request.url.path
        return (
            path in self._allow_paths
            or path.startswith(self._allow_prefixes)
            or request.method == "OPTIONS"
        )

    async def dispatch(self, request: Request, call_next):
        if not self._token:
            # Open mode (no key configured) = local dev only. Loopback callers and
            # always-open paths pass; a remote caller is refused so a keyless public
            # deploy isn't a wide-open shell.
            if self._is_open_path(request) or _peer_is_local(request):
                return await call_next(request)
            return JSONResponse(
                {
                    "error": "server has no API key configured and refuses remote "
                    "requests; set VAULT_API_KEY to enable remote access"
                },
                status_code=403,
            )

        if self._is_open_path(request):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)

        presented = auth.split(" ", 1)[1].strip()
        if not hmac.compare_digest(presented, self._token):
            return JSONResponse({"error": "invalid token"}, status_code=401)

        return await call_next(request)
