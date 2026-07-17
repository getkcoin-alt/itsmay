"""Rotating API key pool with rate-limit awareness.

Used by any client whose provider returns OpenAI-style rate-limit headers
(`x-ratelimit-remaining-*`, `retry-after`). Lets you stack multiple keys from
the same vendor (e.g. several Groq free-tier keys) and survive an exhausted
quota by rotating to the next.

Rotation triggers:
- `mark_rate_limited(retry_after)`  → put current key on cooldown, advance
- `mark_invalid()`                  → put current key on long cooldown (1h)
- proactive: `lease()`/`current()` skip keys whose remaining_tokens/remaining_requests
  fell below floors on the last response

If every key is in cooldown, `lease()` still returns the soonest-available one
rather than raising — let the upstream call hit a 429 and bubble.

Redis persistence (optional)
----------------------------
When ``REDIS_URL`` is configured, cooldown state is written to Redis with TTLs
matching the actual cooldown duration. This way a Railway redeploy doesn't
"forget" that a key is rate-limited and immediately re-hammer it. If Redis is
unavailable, everything works in-memory as before — Redis is an *accelerator*,
never a hard dependency.

Concurrency
-----------
One pool instance is shared across every concurrent turn. `lease()` hands out a
`KeyLease` naming the *exact* key chosen; pass it back to `mark_rate_limited` /
`mark_invalid` / `update_from_headers` so the quota you observed is recorded
against THAT key — not whatever `idx` a concurrently-interleaved turn rotated to
between your request and its response. All mutation runs under a lock so the
rotate-and-record is atomic even if called from worker threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class _KeyState:
    key: str
    cooldown_until: float = 0.0
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    limit_tokens: int | None = None
    reset_tokens: str | None = None
    last_used: float = 0.0
    last_status: int | None = None


@dataclass(slots=True, frozen=True)
class KeyLease:
    """The specific key a caller was handed, plus its index in the pool.

    Hold it across the upstream request and pass it back to the `mark_*` /
    `update_from_headers` calls so the record lands on the key you actually used,
    regardless of how `idx` moved meanwhile.
    """

    key: str
    idx: int


@dataclass(slots=True)
class KeyPool:
    states: list[_KeyState] = field(default_factory=list)
    idx: int = 0
    proactive_request_floor: int = 2
    proactive_token_floor: int = 500
    label: str = "default"
    # Serializes rotate-and-record so concurrent turns can't corrupt cooldowns /
    # quota. Excluded from repr/compare so KeyPool stays cheaply printable/testable.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    @classmethod
    def from_csv(
        cls,
        csv: str,
        *,
        label: str = "default",
        proactive_request_floor: int = 2,
        proactive_token_floor: int = 500,
    ) -> KeyPool:
        keys = [k.strip() for k in (csv or "").split(",") if k.strip()]
        pool = cls(
            states=[_KeyState(k) for k in keys],
            label=label,
            proactive_request_floor=proactive_request_floor,
            proactive_token_floor=proactive_token_floor,
        )
        # Hydrate cooldown state from Redis on construction so freshly-deployed
        # containers know which keys are still rate-limited.
        pool._hydrate_from_redis()
        return pool

    # ── Redis helpers ───────────────────────────────────────────────

    @staticmethod
    def _redis():
        """Return the shared Redis client, or None."""
        try:
            from core.util.redis_pool import get_redis
            return get_redis()
        except Exception:
            return None

    def _redis_key(self, idx: int, suffix: str) -> str:
        return f"keypool:{self.label}:{idx}:{suffix}"

    def _hydrate_from_redis(self) -> None:
        """Load surviving cooldown state from Redis (if available)."""
        r = self._redis()
        if not r or not self.states:
            return
        try:
            now = time.time()
            for i, s in enumerate(self.states):
                ttl = r.ttl(self._redis_key(i, "cooldown"))
                if ttl and ttl > 0:
                    s.cooldown_until = now + ttl
                    log.info(
                        "keypool.redis_hydrate",
                        label=self.label,
                        idx=i,
                        cooldown_remaining=ttl,
                    )
                # Restore remaining token counts
                rt = r.get(self._redis_key(i, "remaining_tokens"))
                if rt is not None:
                    try:
                        s.remaining_tokens = int(rt)
                    except (TypeError, ValueError):
                        pass
                lt = r.get(self._redis_key(i, "limit_tokens"))
                if lt is not None:
                    try:
                        s.limit_tokens = int(lt)
                    except (TypeError, ValueError):
                        pass
        except Exception as exc:
            log.warning("keypool.redis_hydrate_failed", error=str(exc))

    def _persist_cooldown(self, idx: int, cooldown_sec: float) -> None:
        """Write cooldown to Redis with matching TTL (fire-and-forget)."""
        r = self._redis()
        if not r:
            return
        try:
            ttl = max(1, int(cooldown_sec))
            r.setex(self._redis_key(idx, "cooldown"), ttl, "1")
        except Exception:
            pass  # Redis is optional — never block on failure

    def _persist_quota(self, idx: int, s: _KeyState) -> None:
        """Write remaining quota counters to Redis (fire-and-forget)."""
        r = self._redis()
        if not r:
            return
        try:
            pipe = r.pipeline(transaction=False)
            if s.remaining_tokens is not None:
                pipe.setex(self._redis_key(idx, "remaining_tokens"), 300, str(s.remaining_tokens))
            if s.limit_tokens is not None:
                pipe.setex(self._redis_key(idx, "limit_tokens"), 300, str(s.limit_tokens))
            pipe.execute()
        except Exception:
            pass

    # ── core pool logic ─────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        return bool(self.states)

    @property
    def size(self) -> int:
        return len(self.states)

    def _below_floor(self, s: _KeyState) -> bool:
        return (
            s.remaining_requests is not None
            and s.remaining_requests <= self.proactive_request_floor
        ) or (
            s.remaining_tokens is not None
            and s.remaining_tokens <= self.proactive_token_floor
        )

    def _select_idx_locked(self) -> int:
        """Pick the next usable key's index. Caller holds `self._lock`.

        Skips keys on cooldown or below the proactive floors, advancing `idx`
        past them. If every key is gated, returns the soonest-eligible one.
        """
        now = time.time()
        for _ in range(len(self.states)):
            s = self.states[self.idx]
            if s.cooldown_until <= now and not self._below_floor(s):
                return self.idx
            self.idx = (self.idx + 1) % len(self.states)
        # All keys gated — pick the soonest-eligible and hope.
        soonest = min(range(len(self.states)), key=lambda i: self.states[i].cooldown_until)
        self.idx = soonest
        log.warning(
            "keypool.all_gated",
            label=self.label,
            soonest_in=self.states[soonest].cooldown_until - now,
        )
        return soonest

    def lease(self) -> KeyLease | None:
        """Reserve the next usable key. Returns a `KeyLease` (key + its index) to
        pass back to the `mark_*` / `update_from_headers` calls, or None if the
        pool is empty."""
        with self._lock:
            if not self.states:
                return None
            i = self._select_idx_locked()
            s = self.states[i]
            s.last_used = time.time()
            return KeyLease(key=s.key, idx=i)

    def current(self) -> str | None:
        """Convenience wrapper over `lease()` for callers that don't need to
        record quota (e.g. health checks). Prefer `lease()` on the hot path so
        cooldown/quota land on the right key under concurrency."""
        lease = self.lease()
        return lease.key if lease else None

    def seconds_until_available(self) -> float:
        """0.0 if any key is usable right now, else seconds until the soonest one
        comes off cooldown. Lets callers rotate instantly to a fresh key and only
        pause when every key is exhausted."""
        with self._lock:
            if not self.states:
                return 0.0
            now = time.time()
            soonest = min(s.cooldown_until for s in self.states)
            return max(0.0, soonest - now)

    def update_from_headers(self, headers, *, lease: KeyLease | None = None) -> None:
        """Record remaining quota off the response so we can pre-emptively rotate.

        Groq returns the full set: x-ratelimit-{remaining,limit}-{requests,tokens}
        plus x-ratelimit-reset-tokens (e.g. "7m12s"). We keep remaining + limit so
        callers can show "% of today's budget left", and the reset window so they
        can say when it refills. Pass the `lease` from `lease()` so the quota lands
        on the key that served the request.
        """
        with self._lock:
            if not self.states:
                return
            i = lease.idx if lease is not None else self.idx
            s = self.states[i]
            rr = headers.get("x-ratelimit-remaining-requests")
            rt = headers.get("x-ratelimit-remaining-tokens")
            lt = headers.get("x-ratelimit-limit-tokens")
            reset = headers.get("x-ratelimit-reset-tokens")
            if rr is not None:
                try:
                    s.remaining_requests = int(float(rr))
                except (TypeError, ValueError):
                    pass
            if rt is not None:
                try:
                    s.remaining_tokens = int(float(rt))
                except (TypeError, ValueError):
                    pass
            if lt is not None:
                try:
                    s.limit_tokens = int(float(lt))
                except (TypeError, ValueError):
                    pass
            if reset:
                s.reset_tokens = str(reset)
            self._persist_quota(i, s)

    def mark_rate_limited(
        self, retry_after_sec: float | None = None, *, lease: KeyLease | None = None
    ) -> None:
        with self._lock:
            if not self.states:
                return
            i = lease.idx if lease is not None else self.idx
            cooldown = retry_after_sec if (retry_after_sec and retry_after_sec > 0) else 60.0
            s = self.states[i]
            s.cooldown_until = time.time() + cooldown
            s.last_status = 429
            log.warning(
                "keypool.rate_limited",
                label=self.label,
                idx=i,
                cooldown_sec=cooldown,
                pool_size=len(self.states),
            )
            self.idx = (i + 1) % len(self.states)
            self._persist_cooldown(i, cooldown)

    def mark_invalid(self, *, lease: KeyLease | None = None) -> None:
        with self._lock:
            if not self.states:
                return
            i = lease.idx if lease is not None else self.idx
            s = self.states[i]
            cooldown = 3600.0  # 1 hour
            s.cooldown_until = time.time() + cooldown
            s.last_status = 401
            log.warning("keypool.invalid_key", label=self.label, idx=i)
            self.idx = (i + 1) % len(self.states)
            self._persist_cooldown(i, cooldown)

    def status(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "label": self.label,
                "size": len(self.states),
                "keys": [
                    {
                        "idx": i,
                        "active": s.cooldown_until <= now,
                        "cooldown_sec_remaining": max(0.0, s.cooldown_until - now),
                        "remaining_requests": s.remaining_requests,
                        "remaining_tokens": s.remaining_tokens,
                        "limit_tokens": s.limit_tokens,
                        "reset_tokens": s.reset_tokens,
                        "last_status": s.last_status,
                    }
                    for i, s in enumerate(self.states)
                ],
            }


def parse_retry_after(value: str | None) -> float | None:
    """Groq + OpenAI return retry-after in integer seconds."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

