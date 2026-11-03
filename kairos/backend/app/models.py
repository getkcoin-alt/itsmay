"""Database schema. Money and quantities are fixed-point NUMERIC, never floats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

QTY = Numeric(24, 8)
PRICE = Numeric(24, 8)
MONEY = Numeric(20, 4)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def TZDateTime() -> DateTime:  # noqa: N802 - reads like a column type
    """Timezone-aware timestamp column (UTC)."""
    return DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    account: Mapped[Account] = relationship(back_populates="user", uselist=False)
    kyc: Mapped[KycRecord | None] = relationship(back_populates="user", uselist=False)


class Account(Base):
    """One INR cash account per user. `cash_balance` always equals SUM(ledger.amount)."""

    __tablename__ = "accounts"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    cash_balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="account")


class Session_(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class KycRecord(Base):
    """Simulated KYC. Never stores real identity documents."""

    __tablename__ = "kyc_records"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="NOT_SUBMITTED")
    legal_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pan_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    income_band: Mapped[str | None] = mapped_column(String(40), nullable=True)
    derivatives_experience: Mapped[str | None] = mapped_column(String(40), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="kyc")


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    asset_class: Mapped[str] = mapped_column(String(16), index=True)  # stock|crypto|forex|index
    exchange: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))  # quote currency
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_price: Mapped[Decimal] = mapped_column(PRICE)
    volatility: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    decimals: Mapped[int] = mapped_column(Integer, default=2)
    tick_size: Mapped[Decimal] = mapped_column(PRICE)
    lot_size: Mapped[Decimal] = mapped_column(QTY, default=Decimal("1"))
    min_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    has_options: Mapped[bool] = mapped_column(Boolean, default=False)
    price_multiplier: Mapped[Decimal] = mapped_column(Numeric(18, 10), default=Decimal("1"))
    override_from: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_orders_client_id"),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(48), index=True)
    asset_class: Mapped[str] = mapped_column(String(16))  # stock|crypto|forex|option
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    order_type: Mapped[str] = mapped_column(String(8))  # MARKET | LIMIT | EXPIRY
    product: Mapped[str] = mapped_column(String(12))  # DELIVERY | SPOT | MARGIN | NRML
    quantity: Mapped[Decimal] = mapped_column(QTY)  # units (not lots)
    limit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    status: Mapped[str] = mapped_column(String(12))  # OPEN|FILLED|CANCELLED|REJECTED
    filled_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    reserved_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class Position(Base):
    """Net position per user and symbol. Signed quantity: negative = short."""

    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_positions_user_symbol"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(48))
    asset_class: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    avg_price: Mapped[Decimal] = mapped_column(PRICE, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    margin_blocked: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    opened_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class LedgerEntry(Base):
    """Append-only cash ledger (the 'transactions' users see)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(MONEY)  # signed, INR
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    description: Mapped[str] = mapped_column(String(255))
    reference: Mapped[str] = mapped_column(String(40))
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(48), nullable=True)
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
