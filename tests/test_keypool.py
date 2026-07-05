"""IM-2.2 (#8) — usage/cost visibility.

The key pool captures the daily token budget off Groq's rate-limit headers
(`x-ratelimit-{remaining,limit}-tokens`, `x-ratelimit-reset-tokens`), and the
CLI renders it with a percent + a ⚠️ low flag so you see the wall coming.
"""

from __future__ import annotations

from apps.cli import _budget_pct, _format_key_line
from core.util.keypool import KeyPool


def _pool(n: int = 1) -> KeyPool:
    return KeyPool.from_csv(",".join(f"k{i}" for i in range(n)), label="test")


# --- header capture -------------------------------------------------------


def test_update_from_headers_captures_limit_and_reset():
    pool = _pool()
    pool.update_from_headers(
        {
            "x-ratelimit-remaining-tokens": "5000",
            "x-ratelimit-limit-tokens": "100000",
            "x-ratelimit-reset-tokens": "7m12s",
            "x-ratelimit-remaining-requests": "29",
        }
    )
    k = pool.status()["keys"][0]
    assert k["remaining_tokens"] == 5000
    assert k["limit_tokens"] == 100000
    assert k["reset_tokens"] == "7m12s"
    assert k["remaining_requests"] == 29


def test_update_from_headers_tolerates_missing_and_garbage():
    pool = _pool()
    pool.update_from_headers({"x-ratelimit-limit-tokens": "not-a-number"})
    k = pool.status()["keys"][0]
    assert k["limit_tokens"] is None
    assert k["remaining_tokens"] is None
    assert k["reset_tokens"] is None


def test_status_includes_budget_fields_even_before_first_call():
    k = _pool(2).status()["keys"][0]
    assert "limit_tokens" in k and k["limit_tokens"] is None
    assert "reset_tokens" in k and k["reset_tokens"] is None


# --- pure render helpers --------------------------------------------------


def test_budget_pct():
    assert abs(_budget_pct({"remaining_tokens": 5000, "limit_tokens": 100000}) - 5.0) < 1e-6
    assert abs(_budget_pct({"remaining_tokens": 80000, "limit_tokens": 100000}) - 80.0) < 1e-6
    assert _budget_pct({"remaining_tokens": 5, "limit_tokens": 0}) is None  # no div-by-zero
    assert _budget_pct({"remaining_tokens": 5}) is None  # limit unknown
    assert _budget_pct({}) is None


def test_format_key_line_shows_percent_and_low_flag():
    line = _format_key_line(
        {
            "idx": 0,
            "active": True,
            "remaining_tokens": 5000,
            "limit_tokens": 100000,
            "reset_tokens": "7m12s",
        }
    )
    assert "5,000 / 100,000 tokens left (5%)" in line
    assert "low" in line  # under 15%
    assert "resets in 7m12s" in line  # refill surfaced because it's low


def test_format_key_line_healthy_has_no_low_flag_or_refill_noise():
    line = _format_key_line(
        {
            "idx": 1,
            "active": True,
            "remaining_tokens": 80000,
            "limit_tokens": 100000,
            "reset_tokens": "1m",
        }
    )
    assert "80,000 / 100,000 tokens left (80%)" in line
    assert "low" not in line
    assert "resets in" not in line  # healthy active key stays quiet


def test_format_key_line_unknown_limit_falls_back_no_false_alarm():
    line = _format_key_line({"idx": 0, "active": True, "remaining_tokens": 1500})
    assert "1,500 tokens left" in line
    assert "%" not in line
    assert "low" not in line  # can't compute pct → don't cry wolf


def test_format_key_line_cooling_shows_cooldown_and_reset():
    line = _format_key_line(
        {
            "idx": 2,
            "active": False,
            "cooldown_sec_remaining": 42.0,
            "remaining_tokens": 0,
            "limit_tokens": 100000,
            "reset_tokens": "5m",
        }
    )
    assert "cooling" in line
    assert "42s left" in line
    assert "resets in 5m" in line
    assert "low" in line  # 0% is well under 15%


# --- concurrency: lease binds records to the right key (#14) ----------------


def test_lease_returns_key_and_index():
    pool = _pool(3)
    lease = pool.lease()
    assert lease is not None
    assert lease.key == "k0" and lease.idx == 0
    assert pool.lease().key == "k0"  # still usable → same key, not advanced


def test_lease_isolates_concurrent_record_from_rotated_idx():
    """Two interleaved turns share the pool. Turn A leases k0; turn B then
    rate-limits its own key and advances `idx`. A's later header-record must
    still land on k0 (its lease), not on whatever `idx` B moved to — the race
    the old `self.idx`-based recording corrupted."""
    pool = KeyPool.from_csv("k0,k1", label="test")
    a = pool.lease()  # A holds k0 @ idx 0
    b = pool.lease()  # B also holds k0 @ idx 0
    pool.mark_rate_limited(30, lease=b)  # B cools k0, advances idx → 1
    pool.update_from_headers({"x-ratelimit-remaining-tokens": "777"}, lease=a)
    keys = pool.status()["keys"]
    assert keys[0]["remaining_tokens"] == 777  # A's record on its leased key
    assert keys[0]["last_status"] == 429  # k0 cooled by B
    assert keys[1]["remaining_tokens"] is None  # innocent key never touched


def test_mark_without_lease_falls_back_to_idx_backcompat():
    pool = _pool(1)
    pool.mark_rate_limited(15)  # no lease → uses self.idx (back-compat)
    assert pool.status()["keys"][0]["last_status"] == 429


def test_concurrent_lease_and_mark_is_thread_safe():
    import threading

    pool = KeyPool.from_csv(",".join(f"k{i}" for i in range(4)))
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(300):
                lease = pool.lease()
                pool.update_from_headers({"x-ratelimit-remaining-tokens": "10"}, lease=lease)
                pool.mark_rate_limited(1, lease=lease)
        except Exception as e:  # noqa: BLE001 — surface any race as a failure
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors  # no torn state / IndexError under contention
    assert pool.size == 4  # pool intact
    assert 0 <= pool.idx < 4
