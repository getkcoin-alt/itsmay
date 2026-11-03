"""Orders, portfolio, positions, watchlist, wallet and profile endpoints."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..market import service as mkt
from ..models import KycRecord, LedgerEntry, Order, User, WatchlistItem
from ..security import client_ip, current_user, rate_limit
from ..seed import seed_demo_portfolio, seed_fresh_account, wipe_activity
from ..serializers import kyc_json, ledger_json, order_json, user_json
from ..services import wallet
from ..services.audit import audit
from ..trading import engine, portfolio
from ..trading.rules import OrderRejected

router = APIRouter(prefix="/api", tags=["trading"])


class OrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=48)
    side: str = Field(pattern="^(BUY|SELL)$")
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=8)
    lots: int | None = Field(default=None, gt=0, le=10_000)
    limit_price: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=8)
    client_order_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")

    def to_req(self) -> engine.OrderRequest:
        return engine.OrderRequest(
            symbol=self.symbol.strip(),
            side=self.side,
            order_type=self.order_type,
            quantity=self.quantity,
            lots=self.lots,
            limit_price=self.limit_price,
            client_order_id=self.client_order_id,
        )


def _housekeeping(db: Session, user: User) -> None:
    engine.settle_expiries(db, user.id)
    engine.match_open_orders(db, user.id)


@router.post("/orders/preview")
def preview(body: OrderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        a = engine.assess(db, user, body.to_req(), mkt.sim.now_ts())
    except OrderRejected as exc:
        return {"can_execute": False, "rejection_reason": str(exc), "warnings": []}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        db.rollback()
    return engine.assessment_dict(a)


@router.post("/orders", status_code=201)
def place(body: OrderIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(request, "orders", 40, 60, key=user.id)
    try:
        order = engine.place_order(db, user, body.to_req())
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    audit(db, "order.placed", user, "order", order.id,
          {"symbol": order.symbol, "side": order.side, "qty": float(order.quantity), "status": order.status,
           "type": order.order_type}, client_ip(request))
    db.commit()
    return {"order": order_json(db, order), "account": portfolio.summary(db, user)}


@router.get("/orders")
def list_orders(
    status: str | None = Query(default=None, pattern="^(OPEN|FILLED|CANCELLED|REJECTED)$"),
    asset_class: str | None = Query(default=None, pattern="^(stock|crypto|forex|option)$"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _housekeeping(db, user)
    q = select(Order).where(Order.user_id == user.id)
    if status:
        q = q.where(Order.status == status)
    if asset_class:
        q = q.where(Order.asset_class == asset_class)
    rows = db.scalars(q.order_by(Order.created_at.desc()).limit(limit)).all()
    counts = dict(db.execute(select(Order.status, func.count()).where(Order.user_id == user.id).group_by(Order.status)).all())
    return {"orders": [order_json(db, o) for o in rows], "counts": counts}


@router.delete("/orders/{order_id}")
def cancel(order_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        order = engine.cancel_order(db, user, order_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, "order.cancelled", user, "order", order.id, {"symbol": order.symbol}, client_ip(request))
    db.commit()
    return {"order": order_json(db, order)}


# Portfolio ---------------------------------------------------------------------


@router.get("/portfolio/summary")
def portfolio_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _housekeeping(db, user)
    return portfolio.summary(db, user)


@router.get("/portfolio/positions")
def positions(include_closed: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _housekeeping(db, user)
    rows = portfolio.position_rows(db, user, include_closed=include_closed)
    return {"positions": rows, "simulated": True}


@router.get("/portfolio/performance")
def performance(range_: str = Query(default="1M", alias="range", pattern="^(1D|1W|1M|3M|ALL)$"),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    return portfolio.performance(db, user, range_)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _housekeeping(db, user)
    summary = portfolio.summary(db, user)
    rows = portfolio.position_rows(db, user)
    recent = db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(6)).all()
    top = sorted(rows, key=lambda r: r["unrealized_pnl_pct"], reverse=True)
    return {
        "summary": summary,
        "positions": rows,
        "top_performers": top[:4],
        "laggards": list(reversed(top[-3:])) if len(top) > 4 else [],
        "recent_orders": [order_json(db, o) for o in recent],
        "simulated": True,
    }


# Watchlist ---------------------------------------------------------------------


class WatchIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=48)


@router.get("/watchlist")
def watchlist(user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = mkt.sim.now_ts()
    out = []
    for item in db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id).order_by(WatchlistItem.created_at)):
        spec = mkt.get_spec(db, item.symbol)
        if spec and spec.enabled:
            out.append(mkt.quote(spec, t))
    return {"items": out, "simulated": True}


@router.post("/watchlist", status_code=201)
def add_watch(body: WatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sym = body.symbol.upper()
    if mkt.get_spec(db, sym) is None:
        raise HTTPException(404, "Unknown instrument")
    if db.scalar(select(func.count()).where(WatchlistItem.user_id == user.id)) >= 50:
        raise HTTPException(422, "Watchlist is limited to 50 instruments")
    if not db.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == sym)):
        db.add(WatchlistItem(user_id=user.id, symbol=sym))
        db.commit()
    return {"ok": True, "symbol": sym}


@router.delete("/watchlist/{symbol}")
def remove_watch(symbol: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol.upper()))
    if item is None:
        raise HTTPException(404, "Not in watchlist")
    db.delete(item)
    db.commit()
    return {"ok": True}


# Wallet ------------------------------------------------------------------------


class DepositIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method: str = Field(default="SIM_UPI", pattern="^(SIM_UPI|SIM_NETBANKING|SIM_BANK)$")


class WithdrawIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


@router.get("/wallet")
def wallet_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = portfolio.summary(db, user)
    totals = dict(
        db.execute(
            select(LedgerEntry.type, func.coalesce(func.sum(LedgerEntry.amount), 0))
            .where(LedgerEntry.user_id == user.id, LedgerEntry.type.in_(("DEPOSIT", "WITHDRAWAL")))
            .group_by(LedgerEntry.type)
        ).all()
    )
    return {
        "cash_balance": s["cash_balance"],
        "available_cash": s["available_cash"],
        "margin_blocked": s["margin_blocked"],
        "reserved_for_orders": s["reserved_for_orders"],
        "withdrawable": max(0.0, s["available_cash"]),
        "total_deposits": float(totals.get("DEPOSIT", 0)),
        "total_withdrawals": float(-totals.get("WITHDRAWAL", 0)),
        "methods": [{"id": k, "label": v} for k, v in wallet.METHODS.items()],
        "notice": "All funding is simulated. No bank, card, UPI or crypto rail is connected and no real money moves.",
    }


@router.post("/wallet/deposit")
def deposit(body: DepositIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(request, "wallet", 20, 60, key=user.id)
    try:
        out = wallet.deposit(db, user, body.amount, body.method)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    audit(db, "wallet.deposit", user, "ledger", out["reference"], {"amount": out["amount"]}, client_ip(request))
    db.commit()
    return out


@router.post("/wallet/withdraw")
def withdraw(body: WithdrawIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(request, "wallet", 20, 60, key=user.id)
    try:
        out = wallet.withdraw(db, user, body.amount)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    audit(db, "wallet.withdraw", user, "ledger", out["reference"], {"amount": out["amount"]}, client_ip(request))
    db.commit()
    return out


@router.get("/transactions")
def transactions(
    type_: str | None = Query(default=None, alias="type", max_length=32),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _housekeeping(db, user)
    q = select(LedgerEntry).where(LedgerEntry.user_id == user.id)
    if type_:
        q = q.where(LedgerEntry.type.in_(type_.split(",")))
    rows = db.scalars(q.order_by(LedgerEntry.created_at.desc(), LedgerEntry.balance_after).limit(limit)).all()
    return {"transactions": [ledger_json(e) for e in rows]}


# Profile -----------------------------------------------------------------------


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    preferences: dict | None = None

    @field_validator("preferences")
    @classmethod
    def _prefs(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        allowed = {"default_order_type", "confirm_orders", "compact_numbers", "theme_density", "default_landing"}
        return {k: v[k] for k in v if k in allowed and isinstance(v[k], (str, bool, int))}


class KycIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=120)
    pan: str = Field(pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$", description="Dummy PAN-format value only")
    city: str = Field(min_length=2, max_length=80)
    occupation: str = Field(min_length=2, max_length=80)
    income_band: str = Field(max_length=40)
    derivatives_experience: str = Field(max_length=40)
    acknowledge_simulated: bool

    @field_validator("acknowledge_simulated")
    @classmethod
    def _ack(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Please confirm you have not entered real identity information")
        return v


@router.get("/profile")
def profile(user: User = Depends(current_user)):
    return {"user": user_json(user)}


@router.patch("/profile")
def update_profile(body: ProfileIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.full_name:
        user.full_name = body.full_name.strip()
    if body.phone is not None:
        if body.phone and not re.fullmatch(r"\+?[0-9 \-]{7,20}", body.phone):
            raise HTTPException(422, "Invalid phone number format")
        user.phone = body.phone or None
    if body.preferences is not None:
        user.preferences = {**(user.preferences or {}), **body.preferences}
    audit(db, "profile.updated", user, "user", user.id, ip=client_ip(request))
    db.commit()
    return {"user": user_json(user)}


@router.post("/profile/kyc")
def submit_kyc(body: KycIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    kyc = db.get(KycRecord, user.id) or KycRecord(user_id=user.id)
    if kyc.status in ("PENDING", "APPROVED"):
        raise HTTPException(409, f"KYC is already {kyc.status.lower()}")
    kyc.status = "PENDING"
    kyc.legal_name = body.legal_name
    kyc.pan_masked = "XXXXX" + body.pan[5:9] + "X"  # never persist the full value
    kyc.city = body.city
    kyc.occupation = body.occupation
    kyc.income_band = body.income_band
    kyc.derivatives_experience = body.derivatives_experience
    kyc.submitted_at = datetime.now(UTC)
    kyc.reviewed_at = kyc.reviewer_id = kyc.review_note = None
    db.add(kyc)
    audit(db, "kyc.submitted", user, "kyc", user.id, ip=client_ip(request))
    db.commit()
    return {"kyc": kyc_json(kyc)}


@router.post("/profile/reset-demo")
def reset_demo(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(request, "reset-demo", 5, 300, key=user.id)
    wipe_activity(db, user.id)
    if user.email == get_settings().demo_user_email:
        seed_demo_portfolio(db, user)
    else:
        seed_fresh_account(db, user)
    audit(db, "account.reset", user, "user", user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True, "account": portfolio.summary(db, user)}
