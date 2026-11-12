"""Administrator API. Every route requires role=admin (server-side RBAC)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..market import service as mkt
from ..market import sim
from ..models import AuditLog, Instrument, KycRecord, LedgerEntry, Order, Position, Session_, User
from ..security import client_ip, require_admin
from ..seed import seed_fresh_account, wipe_activity
from ..serializers import iso, kyc_json, ledger_json, order_json, user_json
from ..services.audit import audit
from ..services.settings_service import DEFAULTS, get_settings_doc, save_section
from ..trading import portfolio

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    users_total = db.scalar(select(func.count()).select_from(User).where(User.role == "user"))
    active_24h = db.scalar(select(func.count()).select_from(User).where(User.role == "user", User.last_seen_at >= _since(1)))
    active_7d = db.scalar(select(func.count()).select_from(User).where(User.role == "user", User.last_seen_at >= _since(7)))
    disabled = db.scalar(select(func.count()).select_from(User).where(User.status == "disabled"))
    kyc_pending = db.scalar(select(func.count()).select_from(KycRecord).where(KycRecord.status == "PENDING"))
    orders_today = db.scalar(select(func.count()).select_from(Order).where(Order.created_at >= _since(1)))
    filled = db.execute(
        select(Order.asset_class, func.count(), func.coalesce(func.sum(Order.quantity * Order.filled_price), 0), func.coalesce(func.sum(Order.fees), 0))
        .where(Order.status == "FILLED", Order.order_type != "EXPIRY")
        .group_by(Order.asset_class)
    ).all()
    status_counts = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    open_positions = db.scalar(select(func.count()).select_from(Position).where(Position.quantity != 0))
    cash_total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)))
    return {
        "users_total": users_total,
        "active_24h": active_24h,
        "active_7d": active_7d,
        "disabled_users": disabled,
        "kyc_pending": kyc_pending,
        "orders_24h": orders_today,
        "order_status": status_counts,
        "open_positions": open_positions,
        "demo_cash_outstanding": float(cash_total or 0),
        "by_asset_class": [
            {"asset_class": ac, "orders": n, "turnover_quote_ccy": float(v), "fees": float(f)} for ac, n, v, f in filled
        ],
        "fees_total": float(sum(f for *_, f in filled)),
        "simulated": True,
    }


@router.get("/reports")
def reports(days: int = Query(default=30, ge=7, le=120), db: Session = Depends(get_db)):
    since = _since(days)
    orders = db.execute(
        select(Order.created_at, Order.asset_class, Order.status, Order.quantity, Order.filled_price, Order.fees, Order.symbol)
        .where(Order.created_at >= since, Order.order_type != "EXPIRY")
    ).all()
    daily: dict[str, dict] = defaultdict(lambda: {"orders": 0, "filled": 0, "rejected": 0, "fees": 0.0})
    symbols: dict[str, int] = defaultdict(int)
    for created, _ac, status, _qty, _price, fees, sym in orders:
        d = created.astimezone(sim.IST).date().isoformat()
        daily[d]["orders"] += 1
        if status == "FILLED":
            daily[d]["filled"] += 1
            daily[d]["fees"] += float(fees or 0)
            symbols[sym] += 1
        if status == "REJECTED":
            daily[d]["rejected"] += 1
    signups = db.execute(select(User.created_at).where(User.created_at >= since, User.role == "user")).all()
    signup_daily: dict[str, int] = defaultdict(int)
    for (c,) in signups:
        signup_daily[c.astimezone(sim.IST).date().isoformat()] += 1
    flows = db.execute(
        select(LedgerEntry.type, func.coalesce(func.sum(LedgerEntry.amount), 0), func.count())
        .where(LedgerEntry.created_at >= since)
        .group_by(LedgerEntry.type)
    ).all()
    today = datetime.now(sim.IST).date()
    series = []
    for k in range(days - 1, -1, -1):
        d = (today - timedelta(days=k)).isoformat()
        series.append({"date": d, **daily.get(d, {"orders": 0, "filled": 0, "rejected": 0, "fees": 0.0}), "signups": signup_daily.get(d, 0)})
    return {
        "days": days,
        "daily": series,
        "top_instruments": [{"symbol": s, "name": _name(db, s), "fills": n} for s, n in sorted(symbols.items(), key=lambda kv: -kv[1])[:10]],
        "ledger_flows": [{"type": t, "amount": float(a), "count": c} for t, a, c in flows],
        "simulated": True,
    }


def _name(db: Session, symbol: str) -> str:
    from ..serializers import display_name

    return display_name(db, symbol)


# Users ---------------------------------------------------------------------------


@router.get("/users")
def users(q: str | None = Query(default=None, max_length=80), status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    if status:
        stmt = stmt.where(User.status == status)
    rows = db.scalars(stmt.order_by(User.created_at.desc()).limit(500)).all()
    out = []
    for u in rows:
        j = user_json(u)
        acct = u.account
        j["cash_balance"] = float(acct.cash_balance) if acct else 0.0
        j["orders"] = db.scalar(select(func.count()).select_from(Order).where(Order.user_id == u.id))
        j["open_positions"] = db.scalar(select(func.count()).select_from(Position).where(Position.user_id == u.id, Position.quantity != 0))
        out.append(j)
    return {"users": out}


@router.get("/users/{user_id}")
def user_detail(user_id: str, db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    orders = db.scalars(select(Order).where(Order.user_id == u.id).order_by(Order.created_at.desc()).limit(25)).all()
    ledger = db.scalars(select(LedgerEntry).where(LedgerEntry.user_id == u.id).order_by(LedgerEntry.created_at.desc()).limit(25)).all()
    return {
        "user": user_json(u),
        "summary": portfolio.summary(db, u),
        "positions": portfolio.position_rows(db, u),
        "orders": [order_json(db, o) for o in orders],
        "transactions": [ledger_json(e) for e in ledger],
    }


class UserPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    role: str | None = Field(default=None, pattern="^(user|admin)$")


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id:
        raise HTTPException(422, "Administrators cannot change their own status or role")
    changes = {}
    if body.status and body.status != u.status:
        changes["status"] = [u.status, body.status]
        u.status = body.status
        if body.status == "disabled":
            for s in db.scalars(select(Session_).where(Session_.user_id == u.id, Session_.revoked_at.is_(None))):
                s.revoked_at = datetime.now(UTC)
    if body.role and body.role != u.role:
        changes["role"] = [u.role, body.role]
        u.role = body.role
    audit(db, "admin.user_updated", admin, "user", u.id, {"email": u.email, **changes}, client_ip(request))
    db.commit()
    return {"user": user_json(u)}


@router.post("/users/{user_id}/reset")
def reset_user(user_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or u.role == "admin":
        raise HTTPException(404, "User not found")
    wipe_activity(db, u.id)
    seed_fresh_account(db, u)
    audit(db, "admin.user_reset", admin, "user", u.id, {"email": u.email}, client_ip(request))
    db.commit()
    return {"ok": True}


# KYC -----------------------------------------------------------------------------


@router.get("/kyc")
def kyc_list(status: str | None = Query(default=None, pattern="^(NOT_SUBMITTED|PENDING|APPROVED|REJECTED)$"), db: Session = Depends(get_db)):
    stmt = select(KycRecord, User).join(User, User.id == KycRecord.user_id).where(User.role == "user")
    if status:
        stmt = stmt.where(KycRecord.status == status)
    rows = db.execute(stmt.order_by(KycRecord.submitted_at.desc().nullslast())).all()
    return {"records": [{**kyc_json(k), "user_id": u.id, "email": u.email, "full_name": u.full_name} for k, u in rows]}


class KycDecision(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    note: str | None = Field(default=None, max_length=500)


@router.post("/kyc/{user_id}")
def kyc_decide(user_id: str, body: KycDecision, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    k = db.get(KycRecord, user_id)
    if k is None:
        raise HTTPException(404, "KYC record not found")
    if k.status != "PENDING":
        raise HTTPException(409, f"Only PENDING records can be reviewed (status is {k.status})")
    k.status = body.decision
    k.review_note = body.note
    k.reviewed_at = datetime.now(UTC)
    k.reviewer_id = admin.id
    audit(db, f"admin.kyc_{body.decision.lower()}", admin, "kyc", user_id, {"note": body.note}, client_ip(request))
    db.commit()
    return {"kyc": kyc_json(k)}


# Instruments ---------------------------------------------------------------------


@router.get("/assets")
def assets(db: Session = Depends(get_db)):
    t = sim.now_ts()
    out = []
    for i in db.scalars(select(Instrument).order_by(Instrument.sort_order)):
        spec = mkt.load_specs(db).get(i.symbol)
        q = mkt.quote(spec, t) if spec else {}
        raw = sim.round_to_tick(sim.raw_price(i.symbol, float(i.base_price), float(i.volatility), t), float(i.tick_size))
        out.append({
            "symbol": i.symbol, "name": i.name, "asset_class": i.asset_class, "exchange": i.exchange,
            "currency": i.currency, "enabled": i.enabled, "tradable": i.tradable, "has_options": i.has_options,
            "ltp": q.get("ltp"), "change_pct": q.get("change_pct"), "model_price": raw,
            "price_multiplier": float(i.price_multiplier), "override_from": iso(i.override_from),
            "volatility": float(i.volatility), "decimals": i.decimals,
        })
    return {"assets": out}


class AssetPatch(BaseModel):
    enabled: bool | None = None
    target_price: Decimal | None = Field(default=None, gt=0)
    clear_override: bool = False
    volatility: Decimal | None = Field(default=None, ge=Decimal("0.01"), le=Decimal("3"))


@router.patch("/assets/{symbol}")
def patch_asset(symbol: str, body: AssetPatch, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    i = db.get(Instrument, symbol.upper())
    if i is None:
        raise HTTPException(404, "Instrument not found")
    t = sim.now_ts()
    details: dict = {}
    if body.enabled is not None and body.enabled != i.enabled:
        details["enabled"] = [i.enabled, body.enabled]
        i.enabled = body.enabled
    if body.volatility is not None:
        details["volatility"] = [float(i.volatility), float(body.volatility)]
        i.volatility = body.volatility
    if body.clear_override:
        details["override_cleared"] = float(i.price_multiplier)
        i.price_multiplier = Decimal("1")
        i.override_from = None
    if body.target_price is not None:
        raw = sim.raw_price(i.symbol, float(i.base_price), float(i.volatility), t)
        mult = float(body.target_price) / raw
        if not 0.2 <= mult <= 5:
            raise HTTPException(422, "Target price must be within 0.2× – 5× of the model price")
        details["target_price"] = float(body.target_price)
        i.price_multiplier = Decimal(repr(round(mult, 10)))
        i.override_from = datetime.fromtimestamp(t, UTC)
    audit(db, "admin.asset_updated", admin, "instrument", i.symbol, details, client_ip(request))
    db.commit()
    mkt.invalidate_cache()
    mkt.load_specs(db, force=True)
    return {"ok": True, "symbol": i.symbol}


# Orders / transactions -----------------------------------------------------------


@router.get("/orders")
def all_orders(
    status: str | None = Query(default=None, pattern="^(OPEN|FILLED|CANCELLED|REJECTED)$"),
    asset_class: str | None = Query(default=None, pattern="^(stock|crypto|forex|option)$"),
    q: str | None = Query(default=None, max_length=48),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(Order, User.email).join(User, User.id == Order.user_id)
    if status:
        stmt = stmt.where(Order.status == status)
    if asset_class:
        stmt = stmt.where(Order.asset_class == asset_class)
    if q:
        stmt = stmt.where(or_(Order.symbol.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    rows = db.execute(stmt.order_by(Order.created_at.desc()).limit(limit)).all()
    return {"orders": [{**order_json(db, o), "user_email": email} for o, email in rows]}


@router.get("/transactions")
def all_transactions(type_: str | None = Query(default=None, alias="type", max_length=32), q: str | None = Query(default=None, max_length=80),
                     limit: int = Query(default=300, ge=1, le=1000), db: Session = Depends(get_db)):
    stmt = select(LedgerEntry, User.email).join(User, User.id == LedgerEntry.user_id)
    if type_:
        stmt = stmt.where(LedgerEntry.type == type_)
    if q:
        stmt = stmt.where(or_(LedgerEntry.reference.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"), LedgerEntry.symbol.ilike(f"%{q}%")))
    rows = db.execute(stmt.order_by(LedgerEntry.created_at.desc()).limit(limit)).all()
    return {"transactions": [{**ledger_json(e), "user_email": email} for e, email in rows]}


# Settings / audit ----------------------------------------------------------------


class SettingsIn(BaseModel):
    fees: dict | None = None
    trading: dict | None = None
    wallet: dict | None = None


def _validate_section(name: str, value: dict) -> dict:
    template = DEFAULTS[name]
    clean: dict = {}
    for k, v in value.items():
        if k not in template:
            raise HTTPException(422, f"Unknown setting {name}.{k}")
        if isinstance(template[k], dict):
            if not isinstance(v, dict):
                raise HTTPException(422, f"{name}.{k} must be an object")
            clean[k] = {}
            for kk, vv in v.items():
                if kk not in template[k] or not isinstance(vv, (int, float)) or vv < 0:
                    raise HTTPException(422, f"Invalid value for {name}.{k}.{kk}")
                if kk == "pct" and vv > 0.05:
                    raise HTTPException(422, "Percentage fees above 5% are not allowed")
                clean[k][kk] = vv
        elif isinstance(template[k], bool):
            if not isinstance(v, bool):
                raise HTTPException(422, f"{name}.{k} must be true/false")
            clean[k] = v
        else:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise HTTPException(422, f"{name}.{k} must be a positive number")
            if k == "forex_leverage" and not 1 <= v <= 100:
                raise HTTPException(422, "Leverage must be between 1 and 100")
            clean[k] = v
    return clean


@router.get("/settings")
def get_platform_settings(db: Session = Depends(get_db)):
    return {"settings": get_settings_doc(db), "defaults": DEFAULTS}


@router.put("/settings")
def put_platform_settings(body: SettingsIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    current = get_settings_doc(db)
    changed = {}
    for name in ("fees", "trading", "wallet"):
        value = getattr(body, name)
        if value is None:
            continue
        clean = _validate_section(name, value)
        merged = {**current[name]}
        for k, v in clean.items():
            merged[k] = {**merged[k], **v} if isinstance(v, dict) else v
        save_section(db, name, merged, admin.id)
        changed[name] = clean
    audit(db, "admin.settings_updated", admin, "settings", None, changed, client_ip(request))
    db.commit()
    return {"settings": get_settings_doc(db)}


@router.get("/audit-logs")
def audit_logs(action: str | None = Query(default=None, max_length=64), limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"{action}%"))
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {"logs": [{"id": r.id, "actor_email": r.actor_email, "action": r.action, "target_type": r.target_type,
                      "target_id": r.target_id, "details": r.details, "ip": r.ip, "created_at": iso(r.created_at)} for r in rows]}
