"""JSON shapes shared by user and admin APIs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .market import service as mkt
from .models import KycRecord, LedgerEntry, Order, User


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def display_name(db: Session, symbol: str) -> str:
    c = mkt.parse_option_symbol(symbol)
    if c:
        spec = mkt.get_spec(db, c.underlying)
        base = spec.name if spec else c.underlying
        return f"{base} {c.expiry.strftime('%d %b')} {int(c.strike)} {c.kind}"
    spec = mkt.get_spec(db, symbol)
    return spec.name if spec else symbol


def order_json(db: Session, o: Order) -> dict:
    lot = None
    c = mkt.parse_option_symbol(o.symbol)
    if c:
        spec = mkt.get_spec(db, c.underlying)
        lot = float((spec.meta.get("options") or {}).get("lot_size", 1)) if spec else None
    elif o.asset_class == "forex":
        lot = 1000.0
    qty = float(o.quantity)
    return {
        "id": o.id,
        "symbol": o.symbol,
        "name": display_name(db, o.symbol),
        "asset_class": o.asset_class,
        "side": o.side,
        "order_type": o.order_type,
        "product": o.product,
        "quantity": qty,
        "lots": qty / lot if lot else None,
        "limit_price": float(o.limit_price) if o.limit_price is not None else None,
        "status": o.status,
        "filled_price": float(o.filled_price) if o.filled_price is not None else None,
        "filled_at": iso(o.filled_at),
        "fees": float(o.fees or 0),
        "reserved_amount": float(o.reserved_amount or 0),
        "realized_pnl": float(o.realized_pnl or 0),
        "reject_reason": o.reject_reason,
        "created_at": iso(o.created_at),
        "client_order_id": o.client_order_id,
        "user_id": o.user_id,
        "simulated": True,
    }


def ledger_json(e: LedgerEntry) -> dict:
    return {
        "id": e.id,
        "type": e.type,
        "amount": float(e.amount),
        "balance_after": float(e.balance_after),
        "description": e.description,
        "reference": e.reference,
        "order_id": e.order_id,
        "symbol": e.symbol,
        "method": e.method,
        "created_at": iso(e.created_at),
        "user_id": e.user_id,
        "simulated": True,
    }


def kyc_json(k: KycRecord | None) -> dict:
    if k is None:
        return {"status": "NOT_SUBMITTED"}
    return {
        "status": k.status,
        "legal_name": k.legal_name,
        "pan_masked": k.pan_masked,
        "city": k.city,
        "occupation": k.occupation,
        "income_band": k.income_band,
        "derivatives_experience": k.derivatives_experience,
        "submitted_at": iso(k.submitted_at),
        "reviewed_at": iso(k.reviewed_at),
        "review_note": k.review_note,
    }


def user_json(u: User, include_kyc: bool = True) -> dict:
    out = {
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "phone": u.phone,
        "role": u.role,
        "status": u.status,
        "created_at": iso(u.created_at),
        "last_login_at": iso(u.last_login_at),
        "last_seen_at": iso(u.last_seen_at),
        "preferences": u.preferences or {},
        "is_seed": u.is_seed,
    }
    if include_kyc:
        out["kyc"] = kyc_json(u.kyc)
    return out
