"""Simulated order execution engine.

Every state change for a user (orders, positions, cash ledger) happens inside
one database transaction that first takes a row lock on the user's Account.
That serialises concurrent orders per user so balances, reservations and
positions cannot drift apart. No real orders are ever routed anywhere.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..market import service as mkt
from ..market import sim
from ..models import Account, KycRecord, LedgerEntry, Order, Position, User
from ..services.settings_service import get_settings_doc
from .rules import (
    D0,
    OrderRejected,
    Tradable,
    align_tick,
    apply_fill,
    check_quantity,
    compute_fee,
    dec,
    fx_rate,
    margin_for_position,
    market_price,
    money,
    notional_inr,
    resolve,
)


@dataclass
class OrderRequest:
    symbol: str
    side: str  # BUY | SELL
    order_type: str = "MARKET"  # MARKET | LIMIT
    quantity: Decimal | None = None  # units
    lots: int | None = None  # for options / forex
    limit_price: Decimal | None = None
    client_order_id: str | None = None


@dataclass
class Assessment:
    inst: Tradable
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal  # execution (market) or limit price used for checks
    market_price: Decimal
    marketable: bool
    notional_inr: Decimal
    fees: Decimal
    cash_delta: Decimal
    realized_inr: Decimal
    new_qty: Decimal
    new_avg: Decimal
    margin_before: Decimal
    margin_after: Decimal
    available_before: Decimal
    available_after: Decimal
    warnings: list[dict] = field(default_factory=list)
    rejection: str | None = None


def ref(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------


def lock_account(db: Session, user_id: str) -> Account:
    acct = db.scalar(select(Account).where(Account.user_id == user_id).with_for_update())
    if acct is None:
        raise LookupError("Account not found")
    return acct


def blocked_margin(db: Session, user_id: str, exclude_symbol: str | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(Position.margin_blocked), 0)).where(Position.user_id == user_id)
    if exclude_symbol:
        q = q.where(Position.symbol != exclude_symbol)
    return dec(db.scalar(q) or 0)


def reserved_funds(db: Session, user_id: str, exclude_order: str | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(Order.reserved_amount), 0)).where(
        Order.user_id == user_id, Order.status == "OPEN"
    )
    if exclude_order:
        q = q.where(Order.id != exclude_order)
    return dec(db.scalar(q) or 0)


def pending_sell_qty(db: Session, user_id: str, symbol: str, exclude_order: str | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(Order.quantity), 0)).where(
        Order.user_id == user_id, Order.symbol == symbol, Order.status == "OPEN", Order.side == "SELL"
    )
    if exclude_order:
        q = q.where(Order.id != exclude_order)
    return dec(db.scalar(q) or 0)


def post_ledger(
    db: Session,
    acct: Account,
    type_: str,
    amount: Decimal,
    description: str,
    at: datetime,
    order_id: str | None = None,
    symbol: str | None = None,
    method: str | None = None,
    reference: str | None = None,
) -> LedgerEntry:
    amount = money(amount)
    acct.cash_balance = money(dec(acct.cash_balance) + amount)
    entry = LedgerEntry(
        user_id=acct.user_id,
        type=type_,
        amount=amount,
        balance_after=acct.cash_balance,
        description=description,
        reference=reference or ref(type_[:3]),
        order_id=order_id,
        symbol=symbol,
        method=method,
        created_at=at,
    )
    db.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Assessment (shared by preview, placement and limit matching)
# ---------------------------------------------------------------------------


def _quantity(inst: Tradable, req: OrderRequest) -> Decimal:
    if req.lots is not None and inst.asset_class in ("option", "forex"):
        return Decimal(int(req.lots)) * inst.lot_size
    if req.quantity is None:
        raise ValueError("Provide quantity (or lots for options/forex)")
    return dec(req.quantity)


def assess(
    db: Session,
    user: User,
    req: OrderRequest,
    t: float,
    exclude_order: str | None = None,
    for_fill_at: Decimal | None = None,
) -> Assessment:
    side = req.side.upper()
    otype = req.order_type.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if otype not in ("MARKET", "LIMIT"):
        raise ValueError("order_type must be MARKET or LIMIT")
    inst = resolve(db, req.symbol, t)
    qty = _quantity(inst, req)
    check_quantity(inst, qty)
    cfg = get_settings_doc(db)
    trading = cfg["trading"]

    ltp = market_price(db, inst, t)
    limit = None
    if otype == "LIMIT":
        if req.limit_price is None or dec(req.limit_price) <= 0:
            raise ValueError("Limit orders need a positive limit price")
        limit = align_tick(dec(req.limit_price), inst.tick_size)
    marketable = otype == "MARKET" or (side == "BUY" and ltp <= limit) or (side == "SELL" and ltp >= limit)
    if for_fill_at is not None:
        price = for_fill_at
    elif otype == "MARKET" or marketable:
        price = ltp
    else:
        price = limit

    pos = db.scalar(select(Position).where(Position.user_id == user.id, Position.symbol == inst.symbol))
    cur_qty = dec(pos.quantity) if pos else D0
    cur_avg = dec(pos.avg_price) if pos else D0
    margin_before = dec(pos.margin_blocked) if pos else D0
    signed = qty if side == "BUY" else -qty

    new_qty, new_avg, realized_q = apply_fill(cur_qty, cur_avg, signed, price)
    fx = fx_rate(db, inst, t)
    realized_inr = money(realized_q * fx)
    notional = money(notional_inr(db, inst, qty, price, t))
    fees = compute_fee(cfg["fees"], inst.asset_class, notional)

    if inst.asset_class == "forex":
        cash_delta = realized_inr  # margin product: only P&L settles
    else:
        cash_delta = money(-signed * price)  # premium / consideration exchanged

    leverage = int(trading.get("forex_leverage", 20))
    margin_after = margin_for_position(db, inst, new_qty, price, t, leverage)

    acct = db.get(Account, user.id)
    cash = dec(acct.cash_balance)
    other_margin = blocked_margin(db, user.id, exclude_symbol=inst.symbol)
    reserved = reserved_funds(db, user.id, exclude_order=exclude_order)
    available_before = money(cash - other_margin - margin_before - reserved)
    available_after = money(cash + cash_delta - fees - other_margin - margin_after - reserved)

    a = Assessment(
        inst=inst,
        side=side,
        order_type=otype,
        quantity=qty,
        price=price,
        market_price=ltp,
        marketable=marketable,
        notional_inr=notional,
        fees=fees,
        cash_delta=cash_delta,
        realized_inr=realized_inr,
        new_qty=new_qty,
        new_avg=new_avg,
        margin_before=margin_before,
        margin_after=margin_after,
        available_before=available_before,
        available_after=available_after,
    )

    # ---- business rules -------------------------------------------------
    if trading.get("trading_halted"):
        a.rejection = "Trading is temporarily halted by the platform administrator"
    elif trading.get("require_kyc_for_trading"):
        kyc = db.get(KycRecord, user.id)
        if not kyc or kyc.status != "APPROVED":
            a.rejection = "Complete (simulated) KYC verification before trading"
    if a.rejection is None and inst.asset_class == "forex" and trading.get("forex_inr_pairs_only") and inst.currency != "INR":
        a.rejection = "Only INR currency pairs are permitted under the current jurisdiction setting"
    if a.rejection is None and notional > dec(str(trading.get("max_order_value_inr", 5_000_000))):
        a.rejection = f"Order value exceeds the per-order limit of ₹{int(trading['max_order_value_inr']):,}"
    if a.rejection is None and inst.asset_class in ("stock", "crypto") and side == "SELL":
        held = cur_qty - pending_sell_qty(db, user.id, inst.symbol, exclude_order)
        if qty > held:
            a.rejection = (
                f"Insufficient holdings: you can sell up to {held.normalize()} {inst.symbol}"
                " (short selling is not available for this product)"
            )
    if a.rejection is None and inst.asset_class == "option" and new_qty < 0 and new_qty < cur_qty:
        if not trading.get("options_selling_enabled", True):
            a.rejection = "Option writing is disabled by the administrator"
    if a.rejection is None and available_after < 0:
        need = money(-available_after)
        a.rejection = f"Insufficient funds: additional ₹{need:,.2f} required (available ₹{available_before:,.2f})"

    a.warnings = _warnings(db, user, a, t, cash)
    return a


def _warnings(db: Session, user: User, a: Assessment, t: float, cash: Decimal) -> list[dict]:
    w: list[dict] = []
    inst = a.inst
    opening_short = inst.asset_class == "option" and a.new_qty < 0 and a.side == "SELL"
    if opening_short and inst.contract.kind == "CE":
        w.append({
            "level": "danger",
            "code": "UNLIMITED_LOSS",
            "message": "Selling a call option without holding the underlying carries theoretically UNLIMITED loss "
            "if the underlying rises sharply. Margin requirements can increase and positions may need to be closed.",
        })
    elif opening_short and inst.contract.kind == "PE":
        max_loss = money(dec(inst.contract.strike) * a.quantity - a.price * a.quantity)
        w.append({
            "level": "danger",
            "code": "LARGE_LOSS",
            "message": f"Selling a put option can lose up to ₹{max_loss:,.0f} (strike × quantity, less premium) "
            "if the underlying falls to zero. Losses can far exceed the premium received.",
        })
    if inst.asset_class == "option" and a.side == "BUY" and a.new_qty > 0:
        dte = (inst.contract.expiry.timestamp() - t) / 86400
        w.append({
            "level": "warning",
            "code": "PREMIUM_AT_RISK",
            "message": f"Option buyers can lose the entire premium paid. This contract expires in {dte:.1f} days and "
            "loses time value every day.",
        })
    if inst.asset_class == "forex":
        lev = get_settings_doc(db)["trading"]["forex_leverage"]
        w.append({
            "level": "warning",
            "code": "LEVERAGE",
            "message": f"Leveraged product (1:{lev}). A small adverse move can wipe out the blocked margin; "
            "losses are settled against your cash balance.",
        })
    if inst.asset_class == "crypto":
        w.append({
            "level": "info",
            "code": "CRYPTO_VOLATILITY",
            "message": "Crypto assets are highly volatile and unregulated in many jurisdictions. Simulated spot trade only.",
        })
    equity_proxy = max(cash, Decimal(1))
    if a.side == "BUY" and a.notional_inr > equity_proxy * Decimal("0.25") and inst.asset_class in ("stock", "crypto"):
        w.append({
            "level": "info",
            "code": "CONCENTRATION",
            "message": "This order uses more than 25% of your cash balance. Consider diversification.",
        })
    if a.order_type == "LIMIT" and not a.marketable:
        w.append({
            "level": "info",
            "code": "RESTING",
            "message": "Limit price is away from the market; the order will rest as OPEN until the simulated price "
            "reaches it. Required funds are reserved meanwhile.",
        })
    return w


def assessment_dict(a: Assessment) -> dict:
    return {
        "symbol": a.inst.symbol,
        "name": a.inst.name,
        "asset_class": a.inst.asset_class,
        "product": a.inst.product,
        "side": a.side,
        "order_type": a.order_type,
        "quantity": float(a.quantity),
        "lots": float(a.quantity / a.inst.lot_size) if a.inst.asset_class in ("option", "forex") else None,
        "lot_size": float(a.inst.lot_size),
        "price": float(a.price),
        "market_price": float(a.market_price),
        "marketable": a.marketable,
        "currency": a.inst.currency,
        "notional_inr": float(a.notional_inr),
        "fees": float(a.fees),
        "cash_delta": float(a.cash_delta),
        "margin_required": float(a.margin_after),
        "margin_released": float(max(D0, a.margin_before - a.margin_after)),
        "available_before": float(a.available_before),
        "available_after": float(a.available_after),
        "position_after": float(a.new_qty),
        "realized_pnl": float(a.realized_inr),
        "warnings": a.warnings,
        "can_execute": a.rejection is None,
        "rejection_reason": a.rejection,
        "simulated": True,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execute(db: Session, acct: Account, order: Order, a: Assessment, t: float) -> None:
    at = utc(t)
    inst = a.inst
    pos = db.scalar(select(Position).where(Position.user_id == acct.user_id, Position.symbol == inst.symbol))
    if pos is None:
        pos = Position(user_id=acct.user_id, symbol=inst.symbol, asset_class=inst.asset_class, opened_at=at)
        db.add(pos)
    if dec(pos.quantity or 0) == 0:
        pos.opened_at = at
    pos.quantity = a.new_qty
    pos.avg_price = a.new_avg
    pos.realized_pnl = money(dec(pos.realized_pnl or 0) + a.realized_inr)
    pos.margin_blocked = a.margin_after
    pos.updated_at = at

    order.status = "FILLED"
    order.filled_price = a.price
    order.filled_at = at
    order.fees = a.fees
    order.realized_pnl = a.realized_inr
    order.reserved_amount = D0
    order.updated_at = at

    label = f"{a.side.title()} {a.quantity.normalize():f} {inst.symbol} @ {a.price.normalize():f}"
    if inst.asset_class == "forex":
        if a.realized_inr != 0:
            post_ledger(db, acct, "FX_PNL", a.realized_inr, f"Realised P&L · {label}", at, order.id, inst.symbol)
    else:
        type_ = "TRADE_BUY" if a.side == "BUY" else "TRADE_SELL"
        if inst.asset_class == "option":
            type_ = "OPTION_PREMIUM_PAID" if a.side == "BUY" else "OPTION_PREMIUM_RECEIVED"
        post_ledger(db, acct, type_, a.cash_delta, label, at, order.id, inst.symbol)
    if a.fees > 0:
        post_ledger(db, acct, "FEE", -a.fees, f"Simulated brokerage · {inst.symbol}", at, order.id, inst.symbol)


def place_order(db: Session, user: User, req: OrderRequest, at: float | None = None) -> Order:
    """Validate and execute (or rest) an order. Commits. Raises ValueError/LookupError for bad input."""
    t = at or sim.now_ts()
    if req.client_order_id:
        existing = db.scalar(
            select(Order).where(Order.user_id == user.id, Order.client_order_id == req.client_order_id)
        )
        if existing:
            return existing  # idempotent retry
    acct = lock_account(db, user.id)
    try:
        a = assess(db, user, req, t)
    except OrderRejected as exc:
        inst_class = "option" if mkt.parse_option_symbol(req.symbol) else (
            mkt.get_spec(db, req.symbol).asset_class if mkt.get_spec(db, req.symbol) else "unknown"
        )
        order = Order(
            user_id=user.id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            asset_class=inst_class,
            side=req.side.upper(),
            order_type=req.order_type.upper(),
            product="-",
            quantity=dec(req.quantity or req.lots or 0),
            limit_price=req.limit_price,
            status="REJECTED",
            reject_reason=str(exc),
            created_at=utc(t),
        )
        db.add(order)
        db.commit()
        return order

    order = Order(
        user_id=user.id,
        client_order_id=req.client_order_id,
        symbol=a.inst.symbol,
        asset_class=a.inst.asset_class,
        side=a.side,
        order_type=a.order_type,
        product=a.inst.product,
        quantity=a.quantity,
        limit_price=align_tick(dec(req.limit_price), a.inst.tick_size) if a.order_type == "LIMIT" else None,
        status="OPEN",
        created_at=utc(t),
        updated_at=utc(t),
    )
    db.add(order)
    db.flush()

    if a.rejection:
        order.status = "REJECTED"
        order.reject_reason = a.rejection
    elif a.marketable:
        _execute(db, acct, order, a, t)
    else:
        # Rest the order and reserve the funds it would consume at its limit price.
        order.reserved_amount = money(max(D0, a.available_before - a.available_after))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Order).where(Order.user_id == user.id, Order.client_order_id == req.client_order_id)
        )
        if existing:
            return existing
        raise
    return order


def cancel_order(db: Session, user: User, order_id: str) -> Order:
    lock_account(db, user.id)
    order = db.scalar(select(Order).where(Order.id == order_id, Order.user_id == user.id).with_for_update())
    if order is None:
        raise LookupError("Order not found")
    if order.status != "OPEN":
        db.rollback()
        raise ValueError(f"Only OPEN orders can be cancelled (this order is {order.status})")
    order.status = "CANCELLED"
    order.reserved_amount = D0
    order.updated_at = datetime.now(UTC)
    db.commit()
    return order


def match_open_orders(db: Session, user_id: str | None = None, at: float | None = None) -> int:
    """Fill resting limit orders whose limit the simulated price has reached."""
    t = at or sim.now_ts()
    q = select(Order.id, Order.user_id).where(Order.status == "OPEN", Order.order_type == "LIMIT")
    if user_id:
        q = q.where(Order.user_id == user_id)
    filled = 0
    for oid, uid in db.execute(q.order_by(Order.created_at)).all():
        try:
            acct = lock_account(db, uid)
            order = db.get(Order, oid)
            if order is None or order.status != "OPEN":
                db.rollback()
                continue
            contract = mkt.parse_option_symbol(order.symbol)
            if contract and contract.expiry.timestamp() <= t:
                order.status = "CANCELLED"
                order.reject_reason = "Contract expired before the limit price was reached"
                order.reserved_amount = D0
                db.commit()
                continue
            user = db.get(User, uid)
            inst = resolve(db, order.symbol, t)
            ltp = market_price(db, inst, t)
            limit = dec(order.limit_price)
            if (order.side == "BUY" and ltp > limit) or (order.side == "SELL" and ltp < limit):
                db.rollback()
                continue
            req = OrderRequest(order.symbol, order.side, "LIMIT", quantity=dec(order.quantity), limit_price=limit)
            a = assess(db, user, req, t, exclude_order=order.id, for_fill_at=ltp)
            if a.rejection:
                order.status = "REJECTED"
                order.reject_reason = f"At execution: {a.rejection}"
                order.reserved_amount = D0
            else:
                _execute(db, acct, order, a, t)
                filled += 1
            db.commit()
        except (OrderRejected, LookupError, ValueError) as exc:
            db.rollback()
            order = db.get(Order, oid)
            if order and order.status == "OPEN":
                order.status = "REJECTED"
                order.reject_reason = f"At execution: {exc}"
                order.reserved_amount = D0
                db.commit()
    return filled


def settle_expiries(db: Session, user_id: str | None = None, at: float | None = None) -> int:
    """Cash-settle option positions whose expiry has passed, at intrinsic value."""
    t = at or sim.now_ts()
    q = select(Position.id, Position.user_id, Position.symbol).where(
        Position.asset_class == "option", Position.quantity != 0
    )
    if user_id:
        q = q.where(Position.user_id == user_id)
    settled = 0
    for pid, uid, symbol in db.execute(q).all():
        c = mkt.parse_option_symbol(symbol)
        if c is None or c.expiry.timestamp() > t:
            continue
        acct = lock_account(db, uid)
        pos = db.get(Position, pid)
        qty = dec(pos.quantity)
        if qty == 0:
            db.rollback()
            continue
        exp_ts = c.expiry.timestamp()
        price = dec(mkt.option_price(db, c, exp_ts)["price"])
        new_qty, new_avg, realized = apply_fill(qty, dec(pos.avg_price), -qty, price)
        at_dt = utc(exp_ts)
        order = Order(
            user_id=uid,
            symbol=symbol,
            asset_class="option",
            side="SELL" if qty > 0 else "BUY",
            order_type="EXPIRY",
            product="NRML",
            quantity=abs(qty),
            status="FILLED",
            filled_price=price,
            filled_at=at_dt,
            realized_pnl=money(realized),
            created_at=at_dt,
            updated_at=at_dt,
        )
        db.add(order)
        db.flush()
        pos.quantity = new_qty
        pos.avg_price = new_avg
        pos.realized_pnl = money(dec(pos.realized_pnl) + realized)
        pos.margin_blocked = D0
        pos.updated_at = at_dt
        amount = money(qty * price)
        if amount != 0:
            post_ledger(db, acct, "EXPIRY_SETTLEMENT", amount, f"Expiry settlement {symbol} @ {price}", at_dt, order.id, symbol)
        db.commit()
        settled += 1
    return settled
