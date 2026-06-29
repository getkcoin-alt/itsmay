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
