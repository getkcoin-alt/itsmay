"""Lazy Redis connection singleton.

Provides a sync ``redis.Redis`` client (the ``redis-py`` library's thread-safe
connection pool), created lazily from ``REDIS_URL``.  If no URL is configured
the module is a no-op — every accessor returns ``None`` and callers should
treat Redis as an *optional accelerator*, never a hard requirement.

Usage::

    from core.util.redis_pool import get_redis

    r = get_redis()
    if r:
        r.set("key", "value", ex=60)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.config import get_settings
from core.logging import get_logger

if TYPE_CHECKING:
    import redis

log = get_logger(__name__)

_client: redis.Redis | None = None
_lock = threading.Lock()
_initialised = False


def get_redis() -> redis.Redis | None:
    """Return the shared Redis client, or *None* if REDIS_URL is not set.

    Thread-safe.  The connection is established on the first call (lazy) and
    reused for the lifetime of the process.
    """
    global _client, _initialised
    if _initialised:
        return _client
    with _lock:
        if _initialised:
            return _client
        url = get_settings().redis_url
        if not url:
            log.info("redis.disabled", reason="REDIS_URL not set")
            _initialised = True
            return None
        try:
            import redis as _redis_mod

            _client = _redis_mod.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=2,
                retry_on_timeout=True,
            )
            _client.ping()
            log.info("redis.connected", url=url[:30] + "…")
        except Exception as exc:
            log.warning("redis.connect_failed", error=str(exc))
            _client = None
        _initialised = True
        return _client


def close_redis() -> None:
    """Shut down the shared client.  Safe to call if never opened."""
    global _client, _initialised
    with _lock:
        if _client:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
        _initialised = False
