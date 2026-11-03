"""Portfolio valuation. Marks every position to the same simulated prices used
for execution, and rebuilds historical equity by replaying the ledger and fills."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market import service as mkt
from ..market import sim
from ..models import Account, LedgerEntry, Order, Position, User
from .rules import D0, OrderRejected, dec, margin_for_position, resolve

EXTERNAL = ("DEPOSIT", "WITHDRAWAL")


def _ts(dt: datetime) -> float:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def _value(db: Session, asset_class: str, symbol: str, qty: float, avg: float, t: float) -> float:
    """Contribution of a position to equity at time t (INR)."""
    if qty == 0:
        return 0.0
    mark = mkt.mark_price(db, symbol, asset_class, t)
    if asset_class == "forex":
        spec = mkt.get_spec(db, symbol)
        return (mark - avg) * qty * mkt.fx_to_inr(db, spec.currency, t)
    return mark * qty


def position_rows(db: Session, user: User, include_closed: bool = False) -> list[dict]:
    t = sim.now_ts()
    q = select(Position).where(Position.user_id == user.id)
    if not include_closed:
        q = q.where(Position.quantity != 0)
    rows = []
    sod = sim.ist_day_start(t)
    from ..services.settings_service import get_settings_doc

    leverage = int(get_settings_doc(db)["trading"]["forex_leverage"])
    for p in db.scalars(q.order_by(Position.opened_at)).all():
        qty = float(p.quantity)
        avg = float(p.avg_price)
        try:
            mark = mkt.mark_price(db, p.symbol, p.asset_class, t)
        except Exception:  # noqa: BLE001 - instrument removed
            continue
        if p.asset_class == "option":
            c = mkt.parse_option_symbol(p.symbol)
            q_ = mkt.option_quote(db, c, t)
            name = f"{q_['underlying_name']} {c.expiry.strftime('%d %b')} {int(c.strike)} {c.kind}"
            prev = q_["prev_close"]
            fx = 1.0
            currency = "INR"
            extra = {
                "underlying": c.underlying,
                "strike": c.strike,
                "kind": c.kind,
                "expiry": c.expiry.isoformat(),
                "lot_size": q_["lot_size"],
                "lots": abs(qty) / q_["lot_size"],
                "spot": q_["spot"],
                "delta": q_["delta"],
                "iv": q_["iv"],
                "days_to_expiry": max(0.0, (c.expiry.timestamp() - t) / 86400),
            }
            decimals = 2
        else:
            spec = mkt.get_spec(db, p.symbol)
            name = spec.name
            prev = mkt.spec_price(spec, sod)
            fx = mkt.fx_to_inr(db, spec.currency, t)
            currency = spec.currency
            decimals = spec.decimals
            extra = {"sector": spec.sector}
            if p.asset_class == "forex":
                extra["lot_size"] = spec.lot_size
                extra["lots"] = abs(qty) / spec.lot_size
        unreal = (mark - avg) * qty * fx
        cost = abs(qty) * avg * fx
        market_value = mark * qty * fx if p.asset_class != "forex" else unreal
        day_ref = max(prev, 0.0) if _ts(p.opened_at) < sod else avg
        day_pnl = (mark - day_ref) * qty * fx
        live_margin = 0.0
        if qty != 0 and (p.asset_class == "forex" or (p.asset_class == "option" and qty < 0)):
            try:
                inst = resolve(db, p.symbol, t)
                live_margin = float(margin_for_position(db, inst, dec(qty), dec(mark), t, leverage))
            except (OrderRejected, LookupError):
                live_margin = float(p.margin_blocked)
        rows.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "name": name,
                "asset_class": p.asset_class,
                "side": "LONG" if qty > 0 else ("SHORT" if qty < 0 else "FLAT"),
                "quantity": qty,
                "avg_price": avg,
                "ltp": mark,
                "prev_close": prev,
                "currency": currency,
                "decimals": decimals,
                "fx_to_inr": fx,
                "invested": cost,
                "market_value": market_value,
                "unrealized_pnl": unreal,
                "unrealized_pnl_pct": (unreal / cost * 100) if cost else 0.0,
                "day_pnl": day_pnl,
                "realized_pnl": float(p.realized_pnl),
                "margin_blocked": float(p.margin_blocked),
                "margin_live": live_margin,
                "opened_at": p.opened_at.isoformat(),
                **extra,
            }
        )
    return rows


def summary(db: Session, user: User) -> dict:
    t = sim.now_ts()
    acct = db.get(Account, user.id)
    cash = float(acct.cash_balance)
    rows = position_rows(db, user)
    margin = float(db.scalar(select(func.coalesce(func.sum(Position.margin_blocked), 0)).where(Position.user_id == user.id)) or 0)
    reserved = float(
        db.scalar(
            select(func.coalesce(func.sum(Order.reserved_amount), 0)).where(Order.user_id == user.id, Order.status == "OPEN")
        )
        or 0
    )
    realized = float(db.scalar(select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(Position.user_id == user.id)) or 0)
    fees = float(db.scalar(select(func.coalesce(func.sum(Order.fees), 0)).where(Order.user_id == user.id, Order.status == "FILLED")) or 0)
    deposits = float(
        db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.user_id == user.id, LedgerEntry.type.in_(EXTERNAL)))
        or 0
    )

    holdings_value = sum(r["market_value"] for r in rows if r["asset_class"] in ("stock", "crypto") or (r["asset_class"] == "option" and r["quantity"] > 0))
    short_liab = sum(-r["market_value"] for r in rows if r["asset_class"] == "option" and r["quantity"] < 0)
    fx_unreal = sum(r["unrealized_pnl"] for r in rows if r["asset_class"] == "forex")
    invested = sum(r["invested"] for r in rows if r["asset_class"] in ("stock", "crypto") or (r["asset_class"] == "option" and r["quantity"] > 0))
    unrealized = sum(r["unrealized_pnl"] for r in rows)
    equity = cash + holdings_value - short_liab + fx_unreal
    margin_live = sum(r["margin_live"] for r in rows)

    sod = sim.ist_day_start(t)
    equity_sod = equity_at_times(db, user.id, [sod])[0][1]
    flows_today = float(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.user_id == user.id,
                LedgerEntry.type.in_(EXTERNAL),
                LedgerEntry.created_at >= datetime.fromtimestamp(sod, UTC),
            )
        )
        or 0
    )
    day_pnl = equity - equity_sod - flows_today
    base_for_pct = equity_sod if equity_sod > 0 else max(deposits, 1)
    by_class: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["asset_class"] == "forex":
            by_class["forex"] += r["margin_blocked"] + r["unrealized_pnl"]
        elif r["quantity"] > 0:
            by_class[r["asset_class"]] += r["market_value"]
    return {
        "currency": "INR",
        "cash_balance": cash,
        "available_cash": cash - margin - reserved,
        "margin_blocked": margin,
        "margin_live": margin_live,
        "reserved_for_orders": reserved,
        "holdings_value": holdings_value,
        "short_option_liability": short_liab,
        "invested_value": invested,
        "total_value": equity,
        "net_deposits": deposits,
        "overall_pnl": equity - deposits,
        "overall_pnl_pct": ((equity - deposits) / deposits * 100) if deposits else 0.0,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl / base_for_pct * 100 if base_for_pct else 0.0,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "fees_paid": fees,
        "open_positions": len(rows),
        "allocation": [{"asset_class": k, "value": v} for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])]
        + [{"asset_class": "cash", "value": max(cash - margin - reserved, 0.0)}],
        "margin_utilisation_pct": (margin_live / equity * 100) if equity > 0 else 0.0,
        "risk_level": _risk_level(margin_live, equity),
        "timestamp": t,
        "simulated": True,
    }


def _risk_level(margin: float, equity: float) -> str:
    if equity <= 0:
        return "critical"
    u = margin / equity
    if u > 0.8:
        return "high"
    if u > 0.5:
        return "elevated"
    return "normal"


def equity_at_times(db: Session, user_id: str, times: list[float]) -> list[tuple[float, float, float]]:
    """Replay ledger + fills to compute (t, equity, net_deposits) at each time (ascending)."""
    ledger = db.execute(
        select(LedgerEntry.created_at, LedgerEntry.amount, LedgerEntry.type)
        .where(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.created_at)
    ).all()
    fills = db.execute(
        select(Order.filled_at, Order.symbol, Order.asset_class, Order.side, Order.quantity, Order.filled_price)
        .where(Order.user_id == user_id, Order.status == "FILLED")
        .order_by(Order.filled_at)
    ).all()
    events = [(_ts(r[0]), 0, r) for r in ledger] + [(_ts(r[0]), 1, r) for r in fills]
    events.sort(key=lambda e: (e[0], e[1]))
    cash = 0.0
    deposits = 0.0
    pos: dict[str, list] = {}  # symbol -> [asset_class, qty, avg]
    out = []
    i = 0
    for t in sorted(times):
        while i < len(events) and events[i][0] <= t:
            _, kind, r = events[i]
            if kind == 0:
                cash += float(r[1])
                if r[2] in EXTERNAL:
                    deposits += float(r[1])
            else:
                _, symbol, ac, side, qty, price = r
                q = float(qty) * (1 if side == "BUY" else -1)
                cur = pos.setdefault(symbol, [ac, 0.0, 0.0])
                new_q = cur[1] + q
                if cur[1] == 0 or (cur[1] > 0) == (q > 0):
                    cur[2] = (abs(cur[1]) * cur[2] + abs(q) * float(price)) / abs(new_q)
                elif new_q != 0 and (new_q > 0) != (cur[1] > 0):
                    cur[2] = float(price)
                cur[1] = new_q
                if abs(new_q) < 1e-12:
                    cur[1], cur[2] = 0.0, 0.0
            i += 1
        value = cash
        for symbol, (ac, q, avg) in pos.items():
            if q:
                try:
                    value += _value(db, ac, symbol, q, avg, t)
                except Exception:  # noqa: BLE001
                    pass
        out.append((t, value, deposits))
    return out


PERF_RANGES = {"1D": (86400, 300), "1W": (7 * 86400, 3600), "1M": (30 * 86400, 4 * 3600), "3M": (90 * 86400, 86400), "ALL": (None, 86400)}


def performance(db: Session, user: User, range_: str = "1M") -> dict:
    t = sim.now_ts()
    span, step = PERF_RANGES.get(range_, PERF_RANGES["1M"])
    first = db.scalar(select(func.min(LedgerEntry.created_at)).where(LedgerEntry.user_id == user.id))
    start_ts = _ts(first) if first else t
    begin = max(start_ts, t - span) if span else start_ts
    if range_ == "ALL":
        step = max(3600, int((t - begin) / 120))
    times = []
    x = begin
    while x < t:
        times.append(x)
        x += step
    times.append(t)
    pts = equity_at_times(db, user.id, times)
    return {
        "range": range_,
        "points": [{"time": int(a), "equity": round(b, 2), "net_deposits": round(c, 2), "pnl": round(b - c, 2)} for a, b, c in pts],
        "simulated": True,
    }


def holdings_by_symbol(db: Session, user_id: str) -> dict[str, Decimal]:
    rows = db.execute(select(Position.symbol, Position.quantity).where(Position.user_id == user_id, Position.quantity != 0)).all()
    return {s: dec(q) for s, q in rows}


__all__ = ["summary", "position_rows", "performance", "equity_at_times", "holdings_by_symbol", "D0"]
