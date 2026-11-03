"""Seed instruments, platform settings and demo accounts.

Demo portfolios are created by running back-dated orders through the real
trading engine, so every seeded balance, position and P&L figure is consistent
with the ledger and the simulated price history.

Usage:  python -m app.seed            (idempotent)
        python -m app.seed --reset    (re-seed demo accounts)
"""

from __future__ import annotations

import argparse
import secrets
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .market import catalog, sim
from .market import service as mkt
from .models import (
    Account,
    Instrument,
    KycRecord,
    LedgerEntry,
    Order,
    Position,
    User,
    WatchlistItem,
)
from .security import hash_password
from .services import wallet
from .services.settings_service import get_settings_doc
from .trading.engine import OrderRequest, lock_account, place_order, post_ledger

DAY = 86400


def seed_instruments(db: Session) -> int:
    rows: list[Instrument] = []
    order = 0
    for sym, name, lvl, vol, opt in catalog.INDICES:
        order += 1
        meta = {"base_volume": 0}
        if opt:
            meta["options"] = {**opt, "span_pct": 0.065, "floor_pct": 0.03}
        rows.append(Instrument(symbol=sym, name=name, asset_class="index", exchange="NSE" if sym != "SENSEX" else "BSE",
                               currency="INR", base_price=Decimal(str(lvl)), volatility=Decimal(str(vol)), decimals=2,
                               tick_size=Decimal("0.05"), tradable=False, has_options=bool(opt), meta=meta, sort_order=order))
    for sym, name, sector, price, vol, bvol in catalog.STOCKS:
        order += 1
        meta = {"base_volume": bvol}
        opt = catalog.STOCK_OPTIONS.get(sym)
        if opt:
            meta["options"] = {**opt, "span_pct": 0.13, "floor_pct": 0.06}
        rows.append(Instrument(symbol=sym, name=name, asset_class="stock", exchange="NSE", currency="INR", sector=sector,
                               base_price=Decimal(str(price)), volatility=Decimal(str(vol)), decimals=2,
                               tick_size=Decimal("0.05"), has_options=bool(opt), meta=meta, sort_order=order))
    for sym, name, price, vol, decimals, min_qty, bvol in catalog.CRYPTO:
        order += 1
        tick = Decimal(1).scaleb(-decimals)
        rows.append(Instrument(symbol=sym, name=name, asset_class="crypto", exchange="Kairos Sim", currency="INR",
                               sector="Crypto", base_price=Decimal(str(price)), volatility=Decimal(str(vol)),
                               decimals=decimals, tick_size=tick, min_qty=Decimal(min_qty), meta={"base_volume": bvol},
                               sort_order=order))
    for sym, name, ccy, price, vol, decimals, tick, bvol in catalog.FOREX:
        order += 1
        rows.append(Instrument(symbol=sym, name=name, asset_class="forex", exchange="Kairos Sim FX", currency=ccy,
                               sector="Currency", base_price=Decimal(str(price)), volatility=Decimal(str(vol)),
                               decimals=decimals, tick_size=Decimal(tick), lot_size=Decimal(1000), min_qty=Decimal(1000),
                               meta={"base_volume": bvol, "base_ccy": sym[:3], "quote_ccy": ccy}, sort_order=order))
    created = 0
    for r in rows:
        if db.get(Instrument, r.symbol) is None:
            db.add(r)
            created += 1
    db.commit()
    mkt.invalidate_cache()
    mkt.load_specs(db, force=True)
    return created


def _ensure_user(db: Session, email: str, name: str, password: str | None, role: str = "user", seed: bool = True) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=name, role=role, is_seed=seed,
                    password_hash=hash_password(password or secrets.token_urlsafe(24)))
        db.add(user)
        db.flush()
        db.add(Account(user_id=user.id, cash_balance=Decimal("0")))
        db.add(KycRecord(user_id=user.id))
        db.commit()
    return user


def wipe_activity(db: Session, user_id: str) -> None:
    for model in (Order, Position, LedgerEntry):
        db.execute(delete(model).where(model.user_id == user_id))
    acct = db.get(Account, user_id)
    acct.cash_balance = Decimal("0")
    db.commit()


def _at(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def _order(db: Session, user: User, symbol: str, side: str, t: float, qty: str | None = None, lots: int | None = None,
           order_type: str = "MARKET", limit: float | None = None) -> Order:
    req = OrderRequest(symbol=symbol, side=side, order_type=order_type,
                       quantity=Decimal(qty) if qty else None, lots=lots,
                       limit_price=Decimal(str(limit)) if limit else None)
    o = place_order(db, user, req, at=t)
    if o.status == "REJECTED":
        raise RuntimeError(f"Seed order rejected: {symbol} {side}: {o.reject_reason}")
    return o


def _nearest_expiry(db: Session, underlying: str, t: float, min_days: float) -> datetime:
    spec = mkt.get_spec(db, underlying)
    for e in mkt.expiries_for(spec, t):
        if e.timestamp() - t >= min_days * DAY:
            return e
    return mkt.expiries_for(spec, t)[-1]


def _strike(db: Session, underlying: str, t: float, offset_steps: int) -> int:
    spec = mkt.get_spec(db, underlying)
    step = spec.meta["options"]["strike_step"]
    return int(round(mkt.spec_price(spec, t) / step) * step + offset_steps * step)


def seed_demo_portfolio(db: Session, user: User) -> None:
    """Build the flagship demo account: ₹10,00,000 initial deposit and a multi-asset book."""
    now = sim.now_ts()
    initial = Decimal(str(get_settings_doc(db)["wallet"]["initial_demo_balance"]))
    acct = lock_account(db, user.id)
    post_ledger(db, acct, "DEPOSIT", initial, "Initial demo balance credited", _at(now - 46 * DAY),
                method="Simulated bank transfer", reference="DEP-INITIAL")
    db.commit()

    # Equities
    _order(db, user, "RELIANCE", "BUY", now - 45 * DAY, "50")
    _order(db, user, "TCS", "BUY", now - 45 * DAY + 3600, "20")
    _order(db, user, "HDFCBANK", "BUY", now - 44 * DAY, "60")
    _order(db, user, "INFY", "BUY", now - 41 * DAY, "40")
    _order(db, user, "ICICIBANK", "BUY", now - 33 * DAY, "25")
    _order(db, user, "ITC", "BUY", now - 27 * DAY, "100")
    _order(db, user, "RELIANCE", "BUY", now - 18 * DAY, "10")  # averaging
    _order(db, user, "TCS", "SELL", now - 12 * DAY, "5")  # realised P&L
    # Crypto
    _order(db, user, "BTC", "BUY", now - 39 * DAY, "0.01")
    _order(db, user, "ETH", "BUY", now - 36 * DAY, "0.2")
    _order(db, user, "SOL", "BUY", now - 4 * DAY, "4")
    _order(db, user, "XRP", "BUY", now - 15 * DAY, "150")
    _order(db, user, "ETH", "SELL", now - 9 * DAY, "0.05")
    # Wallet activity
    wallet.withdraw(db, user, Decimal("25000"), at=_at(now - 20 * DAY))
    wallet.deposit(db, user, Decimal("50000"), "SIM_UPI", at=_at(now - 7 * DAY))
    # Closed BANKNIFTY option trade for history
    t_bn = now - 10 * DAY
    bn_exp = mkt.expiries_for(mkt.get_spec(db, "BANKNIFTY"), t_bn)[-1]
    bn_sym = mkt.option_symbol("BANKNIFTY", bn_exp, _strike(db, "BANKNIFTY", t_bn, 2), "CE")
    _order(db, user, bn_sym, "BUY", t_bn, lots=2)
    _order(db, user, bn_sym, "SELL", now - 8 * DAY, lots=2)
    # Forex (margin)
    _order(db, user, "USDINR", "BUY", now - 6 * DAY, lots=3)
    _order(db, user, "EURUSD", "SELL", now - 5 * DAY, lots=1)
    _order(db, user, "GBPUSD", "BUY", now - 3 * DAY, lots=1)
    # Live NIFTY options: one long call, one short put
    t_opt = now - 1.5 * DAY
    exp = _nearest_expiry(db, "NIFTY", now, min_days=1.5)
    ce = mkt.option_symbol("NIFTY", exp, _strike(db, "NIFTY", t_opt, 2), "CE")
    pe = mkt.option_symbol("NIFTY", exp, _strike(db, "NIFTY", t_opt, -6), "PE")
    _order(db, user, ce, "BUY", t_opt, lots=2)
    _order(db, user, pe, "SELL", t_opt + 1800, lots=1)
    # Resting limit orders
    infy = mkt.spec_price(mkt.get_spec(db, "INFY"), now)
    _order(db, user, "INFY", "BUY", now - 3600, "20", order_type="LIMIT", limit=round(infy * 0.95, 1))
    btc = mkt.spec_price(mkt.get_spec(db, "BTC"), now)
    _order(db, user, "BTC", "SELL", now - 1800, "0.003", order_type="LIMIT", limit=round(btc * 1.08, 0))

    for sym in ("NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK", "BTC", "ETH", "SOL", "USDINR", "EURUSD", "INFY", "TITAN"):
        if db.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == sym)) is None:
            db.add(WatchlistItem(user_id=user.id, symbol=sym))
    kyc = db.get(KycRecord, user.id)
    kyc.status, kyc.legal_name, kyc.pan_masked, kyc.city = "APPROVED", user.full_name, "XXXXX1234X", "Mumbai"
    kyc.occupation, kyc.income_band, kyc.derivatives_experience = "Salaried", "₹10L–25L", "1–3 years"
    kyc.submitted_at, kyc.reviewed_at, kyc.review_note = _at(now - 47 * DAY), _at(now - 46.5 * DAY), "Simulated auto-approval"
    db.commit()


def seed_fresh_account(db: Session, user: User, amount: Decimal | None = None) -> None:
    if amount is None:
        amount = Decimal(str(get_settings_doc(db)["wallet"]["initial_demo_balance"]))
    acct = lock_account(db, user.id)
    post_ledger(db, acct, "DEPOSIT", amount, "Initial demo balance credited", datetime.now(UTC),
                method="Simulated bank transfer", reference="DEP-INITIAL")
    db.commit()


PERSONAS = [
    ("aarav.mehta@platform.test", "Aarav Mehta", "APPROVED", [("RELIANCE", "40"), ("BTC", "0.01"), ("SBIN", "120")]),
    ("isha.rao@platform.test", "Isha Rao", "APPROVED", [("TCS", "15"), ("ETH", "0.4"), ("TITAN", "12")]),
    ("kabir.singh@platform.test", "Kabir Singh", "PENDING", [("HDFCBANK", "80"), ("SOL", "6")]),
    ("meera.iyer@platform.test", "Meera Iyer", "APPROVED", [("INFY", "60"), ("LT", "10"), ("XRP", "400")]),
    ("rohan.das@platform.test", "Rohan Das", "REJECTED", [("DOGE", "5000")]),
    ("ananya.kapoor@platform.test", "Ananya Kapoor", "PENDING", [("ITC", "300"), ("BNB", "1")]),
    ("vikram.nair@platform.test", "Vikram Nair", "APPROVED", [("BAJFINANCE", "50"), ("ADANIPORTS", "40")]),
    ("sara.khan@platform.test", "Sara Khan", "NOT_SUBMITTED", []),
]


def seed_personas(db: Session) -> None:
    now = sim.now_ts()
    for idx, (email, name, kyc_status, trades) in enumerate(PERSONAS):
        user = _ensure_user(db, email, name, None)
        if db.scalar(select(LedgerEntry.id).where(LedgerEntry.user_id == user.id).limit(1)):
            continue
        start = now - (30 - idx * 3) * DAY
        user.created_at = _at(start - 3600)
        acct = lock_account(db, user.id)
        post_ledger(db, acct, "DEPOSIT", Decimal("1000000"), "Initial demo balance credited", _at(start),
                    method="Simulated bank transfer", reference="DEP-INITIAL")
        db.commit()
        for k, (sym, qty) in enumerate(trades):
            _order(db, user, sym, "BUY", start + (k + 1) * 2 * DAY, qty)
        if idx % 3 == 0:
            _order(db, user, "USDINR", "BUY", now - (idx + 1) * DAY, lots=2)
        kyc = db.get(KycRecord, user.id)
        kyc.status = kyc_status
        if kyc_status != "NOT_SUBMITTED":
            kyc.legal_name, kyc.pan_masked, kyc.city = name, f"XXXXX{1000 + idx * 37}X", ["Pune", "Delhi", "Bengaluru", "Chennai", "Kolkata", "Hyderabad", "Jaipur"][idx % 7]
            kyc.occupation, kyc.income_band, kyc.derivatives_experience = "Professional", "₹5L–10L", "None"
            kyc.submitted_at = _at(start - 1800)
            if kyc_status in ("APPROVED", "REJECTED"):
                kyc.reviewed_at = _at(start - 600)
                kyc.review_note = "Simulated review" if kyc_status == "APPROVED" else "Simulated mismatch in submitted name"
        user.last_login_at = user.last_seen_at = _at(now - idx * 5 * 3600)
        db.commit()


def run(reset: bool = False) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        n = seed_instruments(db)
        print(f"instruments: {n} created")
        admin = _ensure_user(db, s.admin_email, "Platform Administrator", s.admin_password, role="admin")
        kyc = db.get(KycRecord, admin.id)
        kyc.status = "APPROVED"
        db.commit()
        demo = _ensure_user(db, s.demo_user_email, "Priya Sharma", s.demo_user_password)
        if reset:
            wipe_activity(db, demo.id)
        if db.scalar(select(LedgerEntry.id).where(LedgerEntry.user_id == demo.id).limit(1)) is None:
            seed_demo_portfolio(db, demo)
            print("demo portfolio seeded")
        seed_personas(db)
        print("personas seeded")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    run(ap.parse_args().reset)
