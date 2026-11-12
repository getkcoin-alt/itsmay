"""Deterministic market simulation.

Every simulated price is a pure function of (symbol, timestamp). There is no
random state and no background price process, so charts, quotes, order fills
and historical replays always agree with each other, on any number of server
instances, and the demo is identical every time it is shown.

The path is fractal value-noise in log-price space: a stack of octaves with
periods from 15 seconds to a year, each with amplitude proportional to
sqrt(period) so the result scales like a random walk with the instrument's
annualised volatility.
"""

from __future__ import annotations

import hashlib
import math
import struct
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache

YEAR = 365.25 * 86400
IST = timezone(timedelta(hours=5, minutes=30))

# Prices are anchored so that price(ANCHOR) == base_price.
ANCHOR = int(datetime(2026, 11, 1, tzinfo=UTC).timestamp())

# (period seconds, weight). Weight tunes the share of variance per scale.
OCTAVES: tuple[tuple[int, float], ...] = (
    (15, 0.35),
    (60, 0.5),
    (300, 0.6),
    (900, 0.7),
    (3600, 0.8),
    (4 * 3600, 0.85),
    (86400, 0.9),
    (3 * 86400, 0.9),
    (10 * 86400, 0.9),
    (30 * 86400, 0.9),
    (90 * 86400, 0.55),
    (365 * 86400, 0.3),
)


@lru_cache(maxsize=400_000)
def _node(seed: str, octave: int, index: int) -> float:
    """Uniform value in [-1, 1] for a lattice node."""
    digest = hashlib.blake2b(f"{seed}|{octave}|{index}".encode(), digest_size=8).digest()
    (n,) = struct.unpack(">Q", digest)
    return (n / 18446744073709551615.0) * 2.0 - 1.0


def hash01(*parts: object) -> float:
    """Deterministic uniform value in [0, 1) for arbitrary keys."""
    digest = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    (n,) = struct.unpack(">Q", digest)
    return n / 18446744073709551616.0


def _smooth(x: float) -> float:
    return x * x * x * (x * (x * 6 - 15) + 10)  # quintic smoothstep


def _noise(seed: str, octave: int, period: int, t: float) -> float:
    pos = t / period
    i = math.floor(pos)
    frac = _smooth(pos - i)
    a = _node(seed, octave, i)
    b = _node(seed, octave, i + 1)
    return a + (b - a) * frac


def log_path(seed: str, volatility: float, t: float) -> float:
    total = 0.0
    for k, (period, weight) in enumerate(OCTAVES):
        amp = volatility * math.sqrt(period / YEAR) * weight * 1.35
        total += amp * _noise(seed, k, period, t)
    return total


@lru_cache(maxsize=250_000)
def raw_price(seed: str, base_price: float, volatility: float, t: float) -> float:
    """Simulated price at unix time `t` (before admin overrides)."""
    return base_price * math.exp(log_path(seed, volatility, t) - log_path(seed, volatility, ANCHOR))


def ist_day_start(t: float) -> float:
    d = datetime.fromtimestamp(t, IST)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def now_ts() -> float:
    return datetime.now(UTC).timestamp()


def round_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return round(round(value / tick) * tick, 10)


def simulated_volume(symbol: str, base_volume: float, t: float) -> float:
    """Cumulative traded volume today (deterministic, grows through the day)."""
    start = ist_day_start(t)
    elapsed = max(0.0, min(1.0, (t - start) / 86400))
    day_factor = 0.55 + 0.9 * hash01(symbol, "vol", int(start))
    return base_volume * day_factor * (elapsed ** 0.85)


# Black-Scholes ---------------------------------------------------------------


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def black_scholes(kind: str, spot: float, strike: float, years: float, iv: float, rate: float = 0.065) -> dict:
    """Price and Greeks for a European option. kind = 'CE' (call) or 'PE' (put)."""
    if years <= 0 or iv <= 0:
        intrinsic = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if kind == "CE" else (-1.0 if spot < strike else 0.0)
        return {"price": intrinsic, "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    disc = math.exp(-rate * years)
    if kind == "CE":
        price = spot * _ncdf(d1) - strike * disc * _ncdf(d2)
        delta = _ncdf(d1)
        theta = -(spot * _npdf(d1) * iv) / (2 * sqrt_t) - rate * strike * disc * _ncdf(d2)
    else:
        price = strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)
        delta = _ncdf(d1) - 1.0
        theta = -(spot * _npdf(d1) * iv) / (2 * sqrt_t) + rate * strike * disc * _ncdf(-d2)
    gamma = _npdf(d1) / (spot * iv * sqrt_t)
    vega = spot * _npdf(d1) * sqrt_t / 100.0
    return {"price": max(price, 0.0), "delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}
