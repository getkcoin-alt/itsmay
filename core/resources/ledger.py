"""The resource ledger — metering that can refuse.

`ResourceLedger` is the surface the rest of Scrappy uses. It hides the period
arithmetic (a flow is counted per UTC month, a stock cumulatively) so a caller
cannot charge this month's searches against last month's bucket, and it offers
`reserve()` for the common shape: refuse *before* doing expensive work, then give
the reservation back if the work turns out not to happen.

Enforcement is deliberately not advisory. `charge()` raises `CeilingExceeded`
and records nothing, so a breach leaves the ledger exactly as it was.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from core.logging import get_logger
from core.resources.models import (
    CeilingExceeded,
    Resource,
    Usage,
    period_for,
)
from core.resources.store import ResourceStore

log = get_logger(__name__)

#: The account every single-operator install bills to. A multi-customer
#: deployment passes a real key id instead.
DEFAULT_ACCOUNT = "primary"


class ResourceLedger:
    def __init__(self, store: ResourceStore, *, account: str = DEFAULT_ACCOUNT) -> None:
        self.store = store
        self.account = account

    def _account(self, account: str | None) -> str:
        return account or self.account

    # ── ceilings ──────────────────────────────────────────────────────

    async def set_ceiling(
        self, resource: Resource, limit: float, *, account: str | None = None
    ) -> None:
        if limit < 0:
            raise ValueError("a ceiling cannot be negative")
        who = self._account(account)
        await self.store.set_ceiling(who, resource, limit)
        log.info("resources.ceiling_set", account=who, resource=str(resource), limit=limit)

    async def clear_ceiling(self, resource: Resource, *, account: str | None = None) -> None:
        who = self._account(account)
        await self.store.clear_ceiling(who, resource)
        log.info("resources.ceiling_cleared", account=who, resource=str(resource))

    async def ceiling(self, resource: Resource, *, account: str | None = None) -> float | None:
        return await self.store.get_ceiling(self._account(account), resource)

    # ── metering ──────────────────────────────────────────────────────

    async def charge(
        self,
        resource: Resource,
        amount: float = 1.0,
        *,
        account: str | None = None,
        reason: str = "",
        when: datetime | None = None,
    ) -> float:
        """Meter `amount`, or raise `CeilingExceeded` and record nothing."""
        who = self._account(account)
        period = period_for(resource, when)
        try:
            return await self.store.charge(who, resource, period, amount, reason=reason)
        except CeilingExceeded as e:
            log.warning(
                "resources.refused",
                account=who,
                resource=str(resource),
                requested=e.requested,
                used=e.used,
                ceiling=e.ceiling,
            )
            raise

    async def release(
        self,
        resource: Resource,
        amount: float = 1.0,
        *,
        account: str | None = None,
        reason: str = "",
        when: datetime | None = None,
    ) -> float:
        """Give consumption back — a deleted memory, a refunded reservation."""
        return await self.store.release(
            self._account(account),
            resource,
            period_for(resource, when),
            amount,
            reason=reason,
        )

    @asynccontextmanager
    async def reserve(
        self,
        resource: Resource,
        amount: float = 1.0,
        *,
        account: str | None = None,
        reason: str = "",
        keep: bool = True,
    ):
        """Charge up front, and release again if the body raises.

        `keep=True` (the default) suits a resource that is genuinely consumed by
        the attempt — tokens are spent whether or not the caller liked the answer.
        `keep=False` suits a stock the body was about to create: charge first so
        the ceiling is honoured *before* the write, and refund if the write fails.
        """
        await self.charge(resource, amount, account=account, reason=reason)
        try:
            yield
        except Exception:
            await self.release(
                resource, amount, account=account, reason=f"rollback: {reason}"
            )
            raise
        else:
            if not keep:
                await self.release(
                    resource, amount, account=account, reason=f"refund: {reason}"
                )

    # ── reads ─────────────────────────────────────────────────────────

    async def usage(
        self,
        resource: Resource,
        *,
        account: str | None = None,
        when: datetime | None = None,
    ) -> Usage:
        who = self._account(account)
        period = period_for(resource, when)
        used, ceiling = (
            await self.store.used(who, resource, period),
            await self.store.get_ceiling(who, resource),
        )
        return Usage(
            account=who, resource=resource, period=period, used=used, ceiling=ceiling
        )

    async def remaining(
        self, resource: Resource, *, account: str | None = None
    ) -> float | None:
        """Headroom left, or `None` when the resource is uncapped."""
        return (await self.usage(resource, account=account)).remaining

    async def report(self, *, account: str | None = None) -> list[Usage]:
        """Everything metered for this account — the bill."""
        who = self._account(account)
        rows = await self.store.summary(who)
        out: list[Usage] = []
        for resource, period, used in rows:
            ceiling = await self.store.get_ceiling(who, resource)
            out.append(
                Usage(
                    account=who,
                    resource=resource,
                    period=period,
                    used=used,
                    ceiling=ceiling,
                )
            )
        return out
