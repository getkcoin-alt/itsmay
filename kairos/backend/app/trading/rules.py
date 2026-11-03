"""Instrument resolution, fees and margin rules for the simulated trading engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from ..market import service as mkt
from ..market import sim

D0 = Decimal("0")
CENT = Decimal("0.01")


class OrderRejected(Exception):
    """A business-rule rejection. The order is recorded with status REJECTED."""


@dataclass(frozen=True)
class Tradable:
    symbol: str
    name: str
    asset_class: str  # stock | crypto | forex | option
    product: str  # DELIVERY | SPOT | MARGIN | NRML
    currency: str
    lot_size: Decimal
    min_qty: Decimal
    tick_size: Decimal
    decimals: int
    contract: mkt.OptionContract | None = None
    underlying_spec: mkt.Spec | None = None


PRODUCTS = {"stock": "DELIVERY", "crypto": "SPOT", "forex": "MARGIN", "option": "NRML"}


def dec(x: float | str | Decimal, places: int = 8) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(round(float(x), places))) if isinstance(x, float) else Decimal(x)


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def resolve(db: Session, symbol: str, t: float | None = None) -> Tradable:
    """Resolve a symbol to a tradable instrument or raise LookupError / OrderRejected."""
    t = t or sim.now_ts()
    contract = mkt.parse_option_symbol(symbol)
    if contract is not None:
        spec = mkt.get_spec(db, contract.underlying)
        if spec is None or not spec.has_options:
            raise LookupError(f"Unknown option underlying {contract.underlying}")
        if not spec.enabled:
            raise OrderRejected(f"{spec.name} options are disabled by the administrator")
        cfg = spec.meta.get("options") or {}
        step = float(cfg.get("strike_step", 50))
        if contract.strike <= 0 or (contract.strike / step) != int(contract.strike / step):
            raise LookupError("Invalid strike for this underlying")
        valid = {e.date() for e in mkt.expiries_for(spec, t)}
        if contract.expiry.date() not in valid:
            if contract.expiry.timestamp() <= t:
                raise OrderRejected("This contract has expired")
            raise LookupError("Expiry is not listed for this underlying")
        lot = dec(str(cfg.get("lot_size", 1)))
        return Tradable(
            symbol=symbol,
            name=f"{spec.name} {contract.expiry.strftime('%d %b')} {int(contract.strike)} {contract.kind}",
            asset_class="option",
            product="NRML",
            currency="INR",
            lot_size=lot,
            min_qty=lot,
            tick_size=Decimal("0.05"),
            decimals=2,
            contract=contract,
            underlying_spec=spec,
        )
    spec = mkt.get_spec(db, symbol)
    if spec is None or spec.asset_class not in PRODUCTS:
        raise LookupError(f"Unknown or non-tradable instrument {symbol}")
    if not spec.enabled or not spec.tradable:
        raise OrderRejected(f"{spec.symbol} is currently disabled for trading by the administrator")
    return Tradable(
        symbol=spec.symbol,
        name=spec.name,
        asset_class=spec.asset_class,
        product=PRODUCTS[spec.asset_class],
        currency=spec.currency,
        lot_size=dec(str(spec.lot_size)),
        min_qty=dec(str(spec.min_qty)),
        tick_size=dec(str(spec.tick_size)),
        decimals=spec.decimals,
    )


def market_price(db: Session, inst: Tradable, t: float) -> Decimal:
    return dec(mkt.mark_price(db, inst.symbol, inst.asset_class, t))


def fx_rate(db: Session, inst: Tradable, t: float) -> Decimal:
    return dec(mkt.fx_to_inr(db, inst.currency, t), 8)


def notional_inr(db: Session, inst: Tradable, qty: Decimal, price: Decimal, t: float) -> Decimal:
    return abs(qty) * price * fx_rate(db, inst, t)


def compute_fee(fees_cfg: dict, asset_class: str, notional: Decimal) -> Decimal:
    cfg = fees_cfg.get(asset_class, {})
    pct = dec(str(cfg.get("pct", 0)))
    cap = dec(str(cfg.get("cap", 0)))
    flat = dec(str(cfg.get("flat", 0)))
    variable = notional * pct
    if cap > 0:
        variable = min(variable, cap)
    return money(variable + flat)


def short_option_margin(db: Session, inst: Tradable, units: Decimal, t: float) -> Decimal:
    """Simulated SPAN-style margin for |units| of a short option.

    premium + max(span% * spot - OTM amount, floor% * spot), per unit.
    """
    c = inst.contract
    spec = inst.underlying_spec
    cfg = spec.meta.get("options") or {}
    span = float(cfg.get("span_pct", 0.065))
    floor = float(cfg.get("floor_pct", 0.03))
    p = mkt.option_price(db, c, t)
    spot = p["spot"]
    otm = max(0.0, c.strike - spot) if c.kind == "CE" else max(0.0, spot - c.strike)
    per_unit = p["price"] + max(span * spot - otm, floor * spot)
    return money(dec(per_unit) * abs(units))


def margin_for_position(db: Session, inst: Tradable, qty: Decimal, price: Decimal, t: float, leverage: int) -> Decimal:
    if qty == 0:
        return D0
    if inst.asset_class == "forex":
        return money(notional_inr(db, inst, qty, price, t) / Decimal(leverage))
    if inst.asset_class == "option" and qty < 0:
        return short_option_margin(db, inst, qty, t)
    return D0


def apply_fill(qty: Decimal, avg: Decimal, signed: Decimal, price: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Weighted-average position accounting. Returns (new_qty, new_avg, realized_in_quote_ccy)."""
    new_qty = qty + signed
    if qty == 0 or (qty > 0) == (signed > 0):
        new_avg = (abs(qty) * avg + abs(signed) * price) / abs(new_qty)
        return new_qty, new_avg, D0
    closing = min(abs(qty), abs(signed))
    direction = Decimal(1) if qty > 0 else Decimal(-1)
    realized = (price - avg) * closing * direction
    if new_qty == 0:
        return new_qty, D0, realized
    if (new_qty > 0) == (qty > 0):
        return new_qty, avg, realized
    return new_qty, price, realized  # flipped through zero


def check_quantity(inst: Tradable, qty: Decimal) -> None:
    if qty <= 0:
        raise ValueError("Quantity must be greater than zero")
    if inst.asset_class in ("option", "forex"):
        lots = qty / inst.lot_size
        if lots != lots.to_integral_value():
            raise ValueError(f"Quantity must be a whole number of lots (lot size {inst.lot_size.normalize()})")
    else:
        steps = qty / inst.min_qty
        if steps != steps.to_integral_value():
            raise ValueError(f"Quantity must be a multiple of {inst.min_qty.normalize()}")


def align_tick(price: Decimal, tick: Decimal) -> Decimal:
    steps = (price / tick).to_integral_value(rounding=ROUND_HALF_UP)
    return (steps * tick).normalize()


def days_to(ts: float, t: float) -> float:
    return max(0.0, (ts - t) / 86400)


def is_finite(x: float) -> bool:
    return math.isfinite(x)
