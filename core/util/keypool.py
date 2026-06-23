"""Rotating API key pool with rate-limit awareness.

Used by any client whose provider returns OpenAI-style rate-limit headers
(`x-ratelimit-remaining-*`, `retry-after`). Lets you stack multiple keys from
the same vendor (e.g. several Groq free-tier keys) and survive an exhausted
quota by rotating to the next.

Rotation triggers:
- `mark_rate_limited(retry_after)`  → put current key on cooldown, advance
- `mark_invalid()`                  → put current key on long cooldown (1h)
- proactive: `current()` skips keys whose remaining_tokens/remaining_requests
  fell below floors on the last response

If every key is in cooldown, `current()` still returns the soonest-available
one rather than raising — let the upstream call hit a 429 and bubble.
"""

from __future__ import annotations

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
    last_used: float = 0.0
    last_status: int | None = None


@dataclass(slots=True)
class KeyPool:
    states: list[_KeyState] = field(default_factory=list)
    idx: int = 0
    proactive_request_floor: int = 2
    proactive_token_floor: int = 500
    label: str = "default"

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
        return cls(
            states=[_KeyState(k) for k in keys],
            label=label,
            proactive_request_floor=proactive_request_floor,
            proactive_token_floor=proactive_token_floor,
        )

    @property
    def configured(self) -> bool:
        return bool(self.states)

    @property
    def size(self) -> int:
        return len(self.states)

    def current(self) -> str | None:
        """Return the next usable key. Skips keys on cooldown or below proactive floors."""
        if not self.states:
            return None
        now = time.time()
        for _ in range(len(self.states)):
            s = self.states[self.idx]
            below_floor = (
                s.remaining_requests is not None
                and s.remaining_requests <= self.proactive_request_floor
            ) or (
                s.remaining_tokens is not None
                and s.remaining_tokens <= self.proactive_token_floor
            )
            if s.cooldown_until <= now and not below_floor:
                s.last_used = now
                return s.key
            self.idx = (self.idx + 1) % len(self.states)
        # All keys gated — pick the soonest-eligible and hope.
        soonest = min(range(len(self.states)), key=lambda i: self.states[i].cooldown_until)
        self.idx = soonest
        s = self.states[self.idx]
        s.last_used = now
        log.warning("keypool.all_gated", label=self.label, soonest_in=s.cooldown_until - now)
        return s.key

    def update_from_headers(self, headers) -> None:
        """Record remaining quota off the response so we can pre-emptively rotate."""
        if not self.states:
            return
        s = self.states[self.idx]
        rr = headers.get("x-ratelimit-remaining-requests")
        rt = headers.get("x-ratelimit-remaining-tokens")
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

    def mark_rate_limited(self, retry_after_sec: float | None = None) -> None:
        if not self.states:
            return
        cooldown = retry_after_sec if (retry_after_sec and retry_after_sec > 0) else 60.0
        s = self.states[self.idx]
        s.cooldown_until = time.time() + cooldown
        s.last_status = 429
        log.warning(
            "keypool.rate_limited",
            label=self.label,
            idx=self.idx,
            cooldown_sec=cooldown,
            pool_size=len(self.states),
        )
        self.idx = (self.idx + 1) % len(self.states)

    def mark_invalid(self) -> None:
        if not self.states:
            return
        s = self.states[self.idx]
        s.cooldown_until = time.time() + 3600  # 1 hour
        s.last_status = 401
        log.warning("keypool.invalid_key", label=self.label, idx=self.idx)
        self.idx = (self.idx + 1) % len(self.states)

    def status(self) -> dict:
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
