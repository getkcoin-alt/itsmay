"""Typed resource-ledger contracts.

A ledger answers two different questions and it is worth keeping them apart:

* *How much has this account consumed?* — the bill, and the audit trail behind it.
* *May this account consume more?* — the ceiling, enforced before the work runs.

The second is the one that makes a resource ledger more than a counter. A limit
that is checked after the fact is a report, not a ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Kind(StrEnum):
    """Whether a resource accumulates forever or resets each period."""

    #: A quantity *held* — stored memories, bytes on disk. It goes down again
    #: when things are deleted, and it never resets on a calendar boundary.
    STOCK = "stock"
    #: A quantity *spent* — searches run, dollars billed, tokens burned. It only
    #: rises within a period, and a new period starts from zero.
    FLOW = "flow"


class Resource(StrEnum):
    """What the ledger meters.

    These are the four dimensions `resource-ceilings` names — money, compute,
    disk and quota — plus the two product counters the memory service bills on.
    """

    USD = "usd"            # money: external API spend
    TOKENS = "tokens"      # compute: model tokens
    BYTES = "bytes"        # disk: stored payload size
    MEMORIES = "memories"  # quota: durable memories held
    SEARCHES = "searches"  # quota: retrievals served
    CALLS = "calls"        # quota: API/tool invocations

    @property
    def kind(self) -> Kind:
        return Kind.STOCK if self in _STOCKS else Kind.FLOW


_STOCKS = frozenset({Resource.BYTES, Resource.MEMORIES})

#: The period a STOCK is recorded under. Stocks do not reset, so they share one
#: bucket rather than being spread across months.
TOTAL = "total"


def period_for(resource: Resource, when: datetime | None = None) -> str:
    """The bucket `resource` is counted in: a UTC month for flows, `TOTAL` for
    stocks. Deriving this rather than storing it means a ceiling cannot be
    accidentally charged against the wrong month."""
    if resource.kind is Kind.STOCK:
        return TOTAL
    moment = (when or datetime.now(UTC)).astimezone(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


class CeilingExceeded(RuntimeError):
    """Raised instead of performing work that would breach a ceiling.

    Carries the numbers so a caller can tell the operator what to raise, and so
    an API layer can render a useful 402/429 rather than a bare refusal.
    """

    def __init__(
        self,
        account: str,
        resource: Resource,
        *,
        requested: float,
        used: float,
        ceiling: float,
    ) -> None:
        self.account = account
        self.resource = resource
        self.requested = requested
        self.used = used
        self.ceiling = ceiling
        super().__init__(
            f"{account}: {resource} ceiling reached — "
            f"{used:g}/{ceiling:g} used, {requested:g} more requested"
        )


@dataclass(frozen=True, slots=True)
class Ceiling:
    account: str
    resource: Resource
    limit: float

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "resource": str(self.resource),
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class Usage:
    """What one account has consumed of one resource in one period."""

    account: str
    resource: Resource
    period: str
    used: float
    ceiling: float | None = None

    @property
    def remaining(self) -> float | None:
        """`None` when uncapped — which is different from zero remaining, and
        callers must not conflate the two."""
        if self.ceiling is None:
            return None
        return max(0.0, self.ceiling - self.used)

    @property
    def exhausted(self) -> bool:
        return self.ceiling is not None and self.used >= self.ceiling

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "resource": str(self.resource),
            "period": self.period,
            "used": self.used,
            "ceiling": self.ceiling,
            "remaining": self.remaining,
        }
