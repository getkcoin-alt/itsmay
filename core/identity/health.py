"""Health probe — 'did the (re)started server come back up and serve?'.

The verify half of supervised self-modification (IM-5.2). After Scrappy applies a
change and restarts, a supervisor calls `wait_healthy` to confirm the new process
is actually serving `/v1/health`; if it never goes healthy, the supervisor rolls
back (self.rollback) to last-good. Pure + injectable, so it's tested without a
live server.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)


async def probe(
    base_url: str,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 - HTTP client timeout, not an asyncio.timeout
    client: Any = None,
) -> dict:
    """One GET {base_url}/v1/health. `healthy` is True on HTTP 200.

    Pass `client` (an httpx.AsyncClient-like with async `get`) to reuse a session
    or to test; otherwise a short-lived client is created.
    """
    url = f"{base_url.rstrip('/')}/v1/health"
    own = client is None
    if own:
        import httpx

        client = httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.get(url)
        return {"healthy": resp.status_code == 200, "status": resp.status_code}
    except Exception as e:
        return {"healthy": False, "status": None, "error": str(e)[:160]}
    finally:
        if own:
            await client.aclose()


async def wait_healthy(
    base_url: str,
    *,
    attempts: int = 15,
    delay: float = 1.0,
    client: Any = None,
) -> dict:
    """Poll `probe` until healthy or `attempts` are exhausted. Returns the final
    result with `attempts` used — the signal a supervisor rolls back on."""
    result: dict = {"healthy": False, "status": None, "attempts": 0}
    for i in range(1, attempts + 1):
        result = await probe(base_url, client=client)
        result["attempts"] = i
        if result["healthy"]:
            log.info("health.ok", base_url=base_url, attempts=i)
            return result
        if i < attempts:
            await asyncio.sleep(delay)
    log.warning("health.never_came_up", base_url=base_url, attempts=attempts)
    return result
