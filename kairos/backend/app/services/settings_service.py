"""Admin-configurable platform settings (fees, leverage, compliance toggles)."""

from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from ..models import PlatformSetting

DEFAULTS: dict[str, dict] = {
    "fees": {
        # Simulated brokerage: min(notional * pct, cap) + flat, per order.
        "stock": {"pct": 0.0003, "cap": 20.0, "flat": 0.0},
        "option": {"pct": 0.0, "cap": 0.0, "flat": 20.0},
        "crypto": {"pct": 0.001, "cap": 0.0, "flat": 0.0},
        "forex": {"pct": 0.0001, "cap": 0.0, "flat": 10.0},
    },
    "trading": {
        "forex_leverage": 20,
        "max_order_value_inr": 5_000_000,
        "require_kyc_for_trading": False,
        "forex_inr_pairs_only": False,
        "options_selling_enabled": True,
        "trading_halted": False,
    },
    "wallet": {
        "max_single_deposit": 2_500_000,
        "max_balance": 25_000_000,
        "initial_demo_balance": 1_000_000,
    },
}


def get_settings_doc(db: Session) -> dict:
    out = copy.deepcopy(DEFAULTS)
    for row in db.query(PlatformSetting).all():
        if row.key in out and isinstance(row.value, dict):
            _merge(out[row.key], row.value)
    return out


def _merge(base: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def save_section(db: Session, key: str, value: dict, actor_id: str | None) -> None:
    row = db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value, updated_by=actor_id))
    else:
        row.value = value
        row.updated_by = actor_id
