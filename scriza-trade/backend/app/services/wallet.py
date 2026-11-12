"""Simulated wallet. No real money, bank, card or crypto rail is ever touched."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import User
from ..trading.engine import inr_fmt, lock_account, post_ledger, ref
from ..trading.rules import dec, money
from .settings_service import get_settings_doc

METHODS = {"SIM_UPI": "Simulated UPI", "SIM_NETBANKING": "Simulated net banking", "SIM_BANK": "Simulated bank transfer"}


def deposit(db: Session, user: User, amount: Decimal, method: str, at: datetime | None = None) -> dict:
    cfg = get_settings_doc(db)["wallet"]
    amount = money(dec(amount))
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if amount > dec(str(cfg["max_single_deposit"])):
        raise ValueError(f"Maximum single demo deposit is ₹{inr_fmt(cfg['max_single_deposit'], 0)}")
    if method not in METHODS:
        raise ValueError("Unknown simulated funding method")
    acct = lock_account(db, user.id)
    if dec(acct.cash_balance) + amount > dec(str(cfg["max_balance"])):
        raise ValueError(f"Demo cash balance cannot exceed ₹{inr_fmt(cfg['max_balance'], 0)}")
    entry = post_ledger(
        db, acct, "DEPOSIT", amount, f"Demo funds added via {METHODS[method]}", at or datetime.now(UTC),
        method=METHODS[method], reference=ref("DEP"),
    )
    db.commit()
    return {"reference": entry.reference, "amount": float(amount), "balance": float(acct.cash_balance)}


def withdraw(db: Session, user: User, amount: Decimal, at: datetime | None = None) -> dict:
    from ..trading.engine import blocked_margin, reserved_funds

    amount = money(dec(amount))
    if amount <= 0:
        raise ValueError("Amount must be positive")
    acct = lock_account(db, user.id)
    available = dec(acct.cash_balance) - blocked_margin(db, user.id) - reserved_funds(db, user.id)
    if amount > available:
        db.rollback()
        raise ValueError(f"Insufficient withdrawable balance (available ₹{inr_fmt(money(available))})")
    entry = post_ledger(
        db, acct, "WITHDRAWAL", -amount, "Demo funds withdrawn to simulated bank account", at or datetime.now(UTC),
        method=METHODS["SIM_BANK"], reference=ref("WDL"),
    )
    db.commit()
    return {"reference": entry.reference, "amount": float(amount), "balance": float(acct.cash_balance)}
