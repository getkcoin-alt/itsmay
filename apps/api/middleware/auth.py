"""Bearer-token auth middleware.

Compares `Authorization: Bearer <token>` against `VAULT_API_KEY`. Constant-time
compare. Skips a configurable allow-list of paths so health probes / the root
status page remain open.

Intentionally simple — this is a personal-AI deployment with one user. If you
need scopes, multiple users, or token rotation, swap in OAuth2 / JWT here.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


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

    async def dispatch(self, request: Request, call_next):
        if not self._token:
            # No token configured = open mode (local dev). Pass through.
            return await call_next(request)

        path = request.url.path
        if (
            path in self._allow_paths
            or path.startswith(self._allow_prefixes)
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)

        presented = auth.split(" ", 1)[1].strip()
        if not hmac.compare_digest(presented, self._token):
            return JSONResponse({"error": "invalid token"}, status_code=401)

        return await call_next(request)
