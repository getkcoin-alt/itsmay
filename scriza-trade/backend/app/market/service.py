"""Market data service: the single source of simulated prices for the whole app.

Quotes, charts, the options chain, order execution, portfolio valuation and the
WebSocket stream all call `price_at`, so they can never disagree.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Instrument
from . import sim

# ---------------------------------------------------------------------------
# Instrument cache. Instruments change rarely (admin edits); reads are hot.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    symbol: str
    name: str
    asset_class: str
    exchange: str
    currency: str
    sector: str | None
    base_price: float
    volatility: float
    decimals: int
    tick_size: float
    lot_size: float
    min_qty: float
    enabled: bool
    tradable: bool
    has_options: bool
    multiplier: float
    override_from: float | None
    meta: dict = field(default_factory=dict)
    sort_order: int = 0


_cache: dict[str, Spec] = {}
_cache_loaded_at = 0.0
_lock = threading.Lock()
CACHE_TTL = 5.0


def _to_spec(i: Instrument) -> Spec:
    of = i.override_from
    if of is not None and of.tzinfo is None:
        of = of.replace(tzinfo=UTC)
    return Spec(
        symbol=i.symbol,
        name=i.name,
        asset_class=i.asset_class,
        exchange=i.exchange,
        currency=i.currency,
        sector=i.sector,
        base_price=float(i.base_price),
        volatility=float(i.volatility),
        decimals=i.decimals,
        tick_size=float(i.tick_size),
        lot_size=float(i.lot_size),
        min_qty=float(i.min_qty),
        enabled=i.enabled,
        tradable=i.tradable,
        has_options=i.has_options,
        multiplier=float(i.price_multiplier),
        override_from=of.timestamp() if of else None,
        meta=dict(i.meta or {}),
        sort_order=i.sort_order,
    )


def load_specs(db: Session, force: bool = False) -> dict[str, Spec]:
    global _cache, _cache_loaded_at
    if not force and _cache and time.monotonic() - _cache_loaded_at < CACHE_TTL:
        return _cache
    with _lock:
        rows = db.scalars(select(Instrument)).all()
        _cache = {r.symbol: _to_spec(r) for r in rows}
        _cache_loaded_at = time.monotonic()
    return _cache


def invalidate_cache() -> None:
    global _cache_loaded_at
    _cache_loaded_at = 0.0


def get_spec(db: Session, symbol: str) -> Spec | None:
    return load_specs(db).get(symbol)


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------


def spec_price(spec: Spec, t: float) -> float:
    p = sim.raw_price(spec.symbol, spec.base_price, spec.volatility, t)
    if spec.override_from is not None and t >= spec.override_from:
        p *= spec.multiplier
    return sim.round_to_tick(p, spec.tick_size)


def fx_to_inr(db: Session, ccy: str, t: float) -> float:
    """Simulated conversion rate from `ccy` to INR."""
    if ccy == "INR":
        return 1.0
    specs = load_specs(db)
    usdinr = spec_price(specs["USDINR"], t)
    if ccy == "USD":
        return usdinr
    pair = {"JPY": "USDJPY", "CHF": "USDCHF", "CAD": "USDCAD"}.get(ccy)
    if pair and pair in specs:
        return usdinr / spec_price(specs[pair], t)
    direct = {"EUR": "EURINR", "GBP": "GBPINR"}.get(ccy)
    if direct and direct in specs:
        return spec_price(specs[direct], t)
    return 1.0


def day_stats(spec: Spec, t: float) -> dict:
    start = sim.ist_day_start(t)
    prev_close = spec_price(spec, start)
    samples = [spec_price(spec, start + k * 1800) for k in range(int((t - start) // 1800) + 1)]
    ltp = spec_price(spec, t)
    samples.append(ltp)
    return {
        "open": prev_close,
        "prev_close": prev_close,
        "high": max(samples),
        "low": min(samples),
        "ltp": ltp,
    }


def quote(spec: Spec, t: float | None = None) -> dict:
    t = t or sim.now_ts()
    d = day_stats(spec, t)
    ltp = d["ltp"]
    change = ltp - d["prev_close"]
    pct = (change / d["prev_close"] * 100) if d["prev_close"] else 0.0
    spread = max(spec.tick_size, ltp * (0.0004 if spec.asset_class == "crypto" else 0.00008))
    base_vol = float(spec.meta.get("base_volume", 1_000_000))
    w52 = [spec_price(spec, t - k * 7 * 86400) for k in range(53)]
    return {
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "exchange": spec.exchange,
        "currency": spec.currency,
        "sector": spec.sector,
        "decimals": spec.decimals,
        "tick_size": spec.tick_size,
        "lot_size": spec.lot_size,
        "min_qty": spec.min_qty,
        "tradable": spec.tradable and spec.enabled,
        "enabled": spec.enabled,
        "has_options": spec.has_options,
        "ltp": ltp,
        "bid": sim.round_to_tick(ltp - spread / 2, spec.tick_size),
        "ask": sim.round_to_tick(ltp + spread / 2, spec.tick_size),
        "open": d["open"],
        "high": d["high"],
        "low": d["low"],
        "prev_close": d["prev_close"],
        "change": change,
        "change_pct": pct,
        "volume": round(sim.simulated_volume(spec.symbol, base_vol, t)),
        "week52_high": max(w52),
        "week52_low": min(w52),
        "timestamp": t,
        "simulated": True,
    }


INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
RANGES = {
    "1D": ("5m", 86400),
    "1W": ("1h", 7 * 86400),
    "1M": ("4h", 30 * 86400),
    "3M": ("1d", 90 * 86400),
    "6M": ("1d", 180 * 86400),
    "1Y": ("1d", 365 * 86400),
    "5Y": ("1w", 5 * 365 * 86400),
}


def candles(spec: Spec, interval: str, count: int, end: float | None = None) -> list[dict]:
    step = INTERVALS[interval]
    end = end or sim.now_ts()
    last_open = math.floor(end / step) * step
    if interval == "1d" or interval == "1w":
        # Align daily bars to IST midnight.
        last_open = sim.ist_day_start(end)
    base_vol = float(spec.meta.get("base_volume", 1_000_000))
    out = []
    samples = 8
    for k in range(count - 1, -1, -1):
        o_t = last_open - k * step
        c_t = min(o_t + step, end)
        pts = [spec_price(spec, o_t + (c_t - o_t) * j / samples) for j in range(samples + 1)]
        vol_share = step / 86400
        vol = base_vol * vol_share * (0.4 + 1.2 * sim.hash01(spec.symbol, interval, int(o_t)))
        out.append(
            {
                "time": int(o_t),
                "open": pts[0],
                "high": max(pts),
                "low": min(pts),
                "close": pts[-1],
                "volume": round(vol, 2),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

RISK_FREE = 0.065


def expiries_for(spec: Spec, t: float | None = None) -> list[datetime]:
    """Upcoming expiries (Tuesdays 15:30 IST): weekly for weekly underlyings, else monthly."""
    t = t or sim.now_ts()
    cfg = spec.meta.get("options") or {}
    now = datetime.fromtimestamp(t, sim.IST)
    out: list[datetime] = []
    day = now.replace(hour=15, minute=30, second=0, microsecond=0)
    for _ in range(140):
        if day.weekday() == 1 and day.timestamp() > t:
            nxt = day + timedelta(days=7)
            is_monthly = nxt.month != day.month
            if cfg.get("weekly") or is_monthly:
                out.append(day)
        day += timedelta(days=1)
        if len(out) >= (4 if cfg.get("weekly") else 3):
            break
    return out


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiry: datetime
    strike: float
    kind: str  # CE | PE


def option_symbol(underlying: str, expiry: datetime, strike: float, kind: str) -> str:
    return f"{underlying}-{expiry.astimezone(sim.IST).strftime('%Y%m%d')}-{int(strike)}-{kind}"


def parse_option_symbol(symbol: str) -> OptionContract | None:
    parts = symbol.split("-")
    if len(parts) != 4 or parts[3] not in ("CE", "PE"):
        return None
    try:
        d = datetime.strptime(parts[1], "%Y%m%d").replace(hour=15, minute=30, tzinfo=sim.IST)
        strike = float(int(parts[2]))
    except ValueError:
        return None
    return OptionContract(symbol=symbol, underlying=parts[0], expiry=d, strike=strike, kind=parts[3])


def implied_vol(spec: Spec, strike: float, spot: float, t: float) -> float:
    cfg = spec.meta.get("options") or {}
    base = float(cfg.get("base_iv", 0.2))
    regime = 1 + 0.12 * sim._noise(spec.symbol + ":iv", 0, 3 * 86400, t)
    x = math.log(strike / spot)
    smile = 1 - 1.1 * x + 16 * x * x
    return max(0.6 * base, min(3 * base, base * regime * smile))


def option_price(db: Session, contract: OptionContract, t: float | None = None) -> dict:
    t = t or sim.now_ts()
    spec = get_spec(db, contract.underlying)
    if spec is None:
        raise KeyError(contract.underlying)
    exp_ts = contract.expiry.timestamp()
    at = min(t, exp_ts)
    spot = spec_price(spec, at)
    years = max(0.0, (exp_ts - at) / sim.YEAR)
    iv = implied_vol(spec, contract.strike, spot, at)
    g = sim.black_scholes(contract.kind, spot, contract.strike, years, iv, RISK_FREE)
    price = max(0.05, sim.round_to_tick(g["price"], 0.05)) if years > 0 else sim.round_to_tick(g["price"], 0.05)
    return {"price": price, "spot": spot, "iv": iv, "years": years, "expired": t >= exp_ts, **{k: g[k] for k in ("delta", "gamma", "theta", "vega")}}


def option_quote(db: Session, contract: OptionContract, t: float | None = None) -> dict:
    t = t or sim.now_ts()
    spec = get_spec(db, contract.underlying)
    cfg = spec.meta.get("options") or {}
    lot = float(cfg.get("lot_size", 1))
    p = option_price(db, contract, t)
    start = sim.ist_day_start(t)
    prev = option_price(db, contract, start)["price"] if start < contract.expiry.timestamp() else p["price"]
    oi, oi_chg, vol = _oi_volume(spec, contract, p["spot"], lot, t)
    change = p["price"] - prev
    return {
        "symbol": contract.symbol,
        "underlying": contract.underlying,
        "underlying_name": spec.name,
        "expiry": contract.expiry.isoformat(),
        "strike": contract.strike,
        "kind": contract.kind,
        "asset_class": "option",
        "lot_size": lot,
        "ltp": p["price"],
        "prev_close": prev,
        "change": change,
        "change_pct": (change / prev * 100) if prev else 0.0,
        "iv": p["iv"],
        "delta": p["delta"],
        "gamma": p["gamma"],
        "theta": p["theta"],
        "vega": p["vega"],
        "spot": p["spot"],
        "oi": oi,
        "oi_change_pct": oi_chg,
        "volume": vol,
        "expired": p["expired"],
        "decimals": 2,
        "tick_size": 0.05,
        "currency": "INR",
        "simulated": True,
        "timestamp": t,
    }


def _oi_volume(spec: Spec, c: OptionContract, spot: float, lot: float, t: float) -> tuple[int, float, int]:
    step = float((spec.meta.get("options") or {}).get("strike_step", 50))
    dist = (c.strike - spot) / (step * 6)
    # OTM strikes carry more OI (writers), peak at round strikes.
    otm = (c.kind == "CE" and c.strike > spot) or (c.kind == "PE" and c.strike < spot)
    shape = math.exp(-(dist**2)) * (1.35 if otm else 0.7)
    roundness = 1.5 if int(c.strike) % int(step * 10) == 0 else (1.15 if int(c.strike) % int(step * 2) == 0 else 1.0)
    base = 180_000 if spec.symbol == "NIFTY" else 60_000
    noise = 0.6 + 0.8 * sim.hash01(c.symbol, "oi")
    oi_lots = base * shape * roundness * noise / max(lot / 75, 1)
    elapsed = (t - sim.ist_day_start(t)) / 86400
    oi_chg = (sim.hash01(c.symbol, "oichg", int(sim.ist_day_start(t))) - 0.4) * 60 * max(elapsed, 0.05)
    vol_lots = oi_lots * (1.5 + 2 * sim.hash01(c.symbol, "v", int(sim.ist_day_start(t)))) * max(elapsed, 0.02)
    return int(oi_lots) * int(lot), round(oi_chg, 2), int(vol_lots) * int(lot)


def option_chain(db: Session, underlying: str, expiry: datetime, strikes_each_side: int = 12) -> dict:
    spec = get_spec(db, underlying)
    cfg = spec.meta.get("options") or {}
    step = float(cfg.get("strike_step", 50))
    t = sim.now_ts()
    spot = spec_price(spec, t)
    atm = round(spot / step) * step
    rows = []
    for k in range(-strikes_each_side, strikes_each_side + 1):
        strike = atm + k * step
        row = {"strike": strike}
        for kind in ("CE", "PE"):
            c = OptionContract(option_symbol(underlying, expiry, strike, kind), underlying, expiry, strike, kind)
            row[kind] = option_quote(db, c, t)
        rows.append(row)
    total_ce = sum(r["CE"]["oi"] for r in rows)
    total_pe = sum(r["PE"]["oi"] for r in rows)
    return {
        "underlying": underlying,
        "underlying_name": spec.name,
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry.isoformat(),
        "lot_size": float(cfg.get("lot_size", 1)),
        "strike_step": step,
        "pcr": round(total_pe / total_ce, 2) if total_ce else None,
        "rows": rows,
        "timestamp": t,
        "simulated": True,
    }


def mark_price(db: Session, symbol: str, asset_class: str, t: float | None = None) -> float:
    """Mark-to-market price used by the portfolio engine."""
    t = t or sim.now_ts()
    if asset_class == "option":
        c = parse_option_symbol(symbol)
        return option_price(db, c, t)["price"]
    return spec_price(get_spec(db, symbol), t)


def to_decimal(value: float, places: int = 8) -> Decimal:
    return Decimal(repr(round(value, places)))


def utc_from_ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)
