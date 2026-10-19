"""Persistence for the resource ledger.

Two tables do the work and one records why:

* `resource_ceilings` — the limit per (account, resource).
* `resource_totals`   — the running total per (account, resource, period).
* `resource_events`   — append-only, every charge and release, for the bill.

The important property is that **checking a ceiling and recording a charge
happen in one transaction**. Read-then-write across two statements lets two
concurrent requests both observe "under the limit" and both commit, which is
how a ceiling silently becomes a suggestion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

import asyncpg

from core.memory.sqlite_util import SqliteConnection, connect, now_iso, restrict_file_perms
from core.resources.models import CeilingExceeded, Resource

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_ceilings (
    account      TEXT NOT NULL,
    resource     TEXT NOT NULL,
    limit_amount REAL NOT NULL,
    PRIMARY KEY (account, resource)
);

CREATE TABLE IF NOT EXISTS resource_totals (
    account  TEXT NOT NULL,
    resource TEXT NOT NULL,
    period   TEXT NOT NULL,
    used     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account, resource, period)
);

CREATE TABLE IF NOT EXISTS resource_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    account  TEXT NOT NULL,
    resource TEXT NOT NULL,
    period   TEXT NOT NULL,
    amount   REAL NOT NULL,
    reason   TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS resource_events_account_idx
    ON resource_events (account, resource, period);
"""


class ResourceStore(Protocol):
    async def set_ceiling(self, account: str, resource: Resource, limit: float) -> None: ...

    async def get_ceiling(self, account: str, resource: Resource) -> float | None: ...

    async def clear_ceiling(self, account: str, resource: Resource) -> None: ...

    async def charge(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float:
        """Record `amount` against the ceiling atomically, returning the new total.

        Raises `CeilingExceeded` without recording anything when the charge would
        breach the limit.
        """
        ...

    async def release(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float: ...

    async def used(self, account: str, resource: Resource, period: str) -> float: ...

    async def summary(self, account: str) -> list[tuple[Resource, str, float]]: ...


class SqliteResourceStore:
    """Local sovereign ledger, stored beside the SQLite memory database.

    Atomicity comes from `SqliteConnection.cursor(write=True)`: it holds the
    store's lock for the whole block and commits once, so the read and the write
    inside a charge cannot interleave with another charge on this store.
    """

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser())
        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        with connect(self.path) as conn:
            conn.executescript(_SQLITE_SCHEMA)
            conn.commit()
        if not existed:
            restrict_file_perms(self.path)
        self._db = SqliteConnection(self.path)

    # ── ceilings ──────────────────────────────────────────────────────

    async def set_ceiling(self, account: str, resource: Resource, limit: float) -> None:
        await asyncio.to_thread(self._set_ceiling_sync, account, resource, limit)

    def _set_ceiling_sync(self, account: str, resource: Resource, limit: float) -> None:
        with self._db.cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO resource_ceilings (account, resource, limit_amount)
                VALUES (?, ?, ?)
                ON CONFLICT(account, resource) DO UPDATE
                SET limit_amount = excluded.limit_amount
                """,
                (account, str(resource), float(limit)),
            )

    async def get_ceiling(self, account: str, resource: Resource) -> float | None:
        return await asyncio.to_thread(self._get_ceiling_sync, account, resource)

    def _get_ceiling_sync(self, account: str, resource: Resource) -> float | None:
        with self._db.cursor() as cur:
            row = cur.execute(
                "SELECT limit_amount FROM resource_ceilings "
                "WHERE account = ? AND resource = ?",
                (account, str(resource)),
            ).fetchone()
        return float(row["limit_amount"]) if row else None

    async def clear_ceiling(self, account: str, resource: Resource) -> None:
        await asyncio.to_thread(self._clear_ceiling_sync, account, resource)

    def _clear_ceiling_sync(self, account: str, resource: Resource) -> None:
        with self._db.cursor(write=True) as cur:
            cur.execute(
                "DELETE FROM resource_ceilings WHERE account = ? AND resource = ?",
                (account, str(resource)),
            )

    # ── charge / release ──────────────────────────────────────────────

    async def charge(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float:
        return await asyncio.to_thread(
            self._charge_sync, account, resource, period, amount, reason
        )

    def _charge_sync(
        self, account: str, resource: Resource, period: str, amount: float, reason: str
    ) -> float:
        if amount < 0:
            raise ValueError("charge amount must be >= 0; use release() to give back")
        key = (account, str(resource), period)
        with self._db.cursor(write=True) as cur:
            row = cur.execute(
                "SELECT limit_amount FROM resource_ceilings "
                "WHERE account = ? AND resource = ?",
                (account, str(resource)),
            ).fetchone()
            ceiling = float(row["limit_amount"]) if row else None

            row = cur.execute(
                "SELECT used FROM resource_totals "
                "WHERE account = ? AND resource = ? AND period = ?",
                key,
            ).fetchone()
            used = float(row["used"]) if row else 0.0

            if ceiling is not None and used + amount > ceiling:
                # Raising inside the write block rolls the transaction back, so a
                # refused charge leaves no total and no event behind.
                raise CeilingExceeded(
                    account, resource, requested=amount, used=used, ceiling=ceiling
                )

            new_total = used + amount
            cur.execute(
                """
                INSERT INTO resource_totals (account, resource, period, used)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account, resource, period) DO UPDATE SET used = excluded.used
                """,
                (*key, new_total),
            )
            cur.execute(
                "INSERT INTO resource_events (account, resource, period, amount, reason, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (*key, amount, reason, now_iso()),
            )
        return new_total

    async def release(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float:
        return await asyncio.to_thread(
            self._release_sync, account, resource, period, amount, reason
        )

    def _release_sync(
        self, account: str, resource: Resource, period: str, amount: float, reason: str
    ) -> float:
        if amount < 0:
            raise ValueError("release amount must be >= 0")
        key = (account, str(resource), period)
        with self._db.cursor(write=True) as cur:
            row = cur.execute(
                "SELECT used FROM resource_totals "
                "WHERE account = ? AND resource = ? AND period = ?",
                key,
            ).fetchone()
            used = float(row["used"]) if row else 0.0
            # Clamped at zero: a double-release must not mint headroom that was
            # never consumed.
            new_total = max(0.0, used - amount)
            cur.execute(
                """
                INSERT INTO resource_totals (account, resource, period, used)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account, resource, period) DO UPDATE SET used = excluded.used
                """,
                (*key, new_total),
            )
            cur.execute(
                "INSERT INTO resource_events (account, resource, period, amount, reason, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (*key, -amount, reason, now_iso()),
            )
        return new_total

    # ── reads ─────────────────────────────────────────────────────────

    async def used(self, account: str, resource: Resource, period: str) -> float:
        return await asyncio.to_thread(self._used_sync, account, resource, period)

    def _used_sync(self, account: str, resource: Resource, period: str) -> float:
        with self._db.cursor() as cur:
            row = cur.execute(
                "SELECT used FROM resource_totals "
                "WHERE account = ? AND resource = ? AND period = ?",
                (account, str(resource), period),
            ).fetchone()
        return float(row["used"]) if row else 0.0

    async def summary(self, account: str) -> list[tuple[Resource, str, float]]:
        return await asyncio.to_thread(self._summary_sync, account)

    def _summary_sync(self, account: str) -> list[tuple[Resource, str, float]]:
        with self._db.cursor() as cur:
            rows = cur.execute(
                "SELECT resource, period, used FROM resource_totals "
                "WHERE account = ? ORDER BY resource, period",
                (account,),
            ).fetchall()
        out: list[tuple[Resource, str, float]] = []
        for r in rows:
            try:
                resource = Resource(r["resource"])
            except ValueError:
                # A row written by a newer version that meters something this
                # build doesn't know about. Skip it rather than crash the bill.
                continue
            out.append((resource, r["period"], float(r["used"])))
        return out

    def close(self) -> None:
        self._db.close()


class PostgresResourceStore:
    """The same ledger on the durable Postgres that backs Vault Zeta.

    Atomicity is explicit here: each charge materialises its total row, takes a
    row lock with `FOR UPDATE`, and only then compares against the ceiling. The
    `ON CONFLICT DO NOTHING` first step matters — `FOR UPDATE` on a row that does
    not exist locks nothing, so without it two concurrent first charges would
    both pass the check.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def set_ceiling(self, account: str, resource: Resource, limit: float) -> None:
        await self.pool.execute(
            """
            INSERT INTO resource_ceilings (account, resource, limit_amount)
            VALUES ($1, $2, $3)
            ON CONFLICT (account, resource) DO UPDATE
            SET limit_amount = EXCLUDED.limit_amount
            """,
            account,
            str(resource),
            float(limit),
        )

    async def get_ceiling(self, account: str, resource: Resource) -> float | None:
        value = await self.pool.fetchval(
            "SELECT limit_amount FROM resource_ceilings WHERE account = $1 AND resource = $2",
            account,
            str(resource),
        )
        return float(value) if value is not None else None

    async def clear_ceiling(self, account: str, resource: Resource) -> None:
        await self.pool.execute(
            "DELETE FROM resource_ceilings WHERE account = $1 AND resource = $2",
            account,
            str(resource),
        )

    async def charge(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float:
        if amount < 0:
            raise ValueError("charge amount must be >= 0; use release() to give back")
        return await self._apply(account, resource, period, amount, reason, charging=True)

    async def release(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        *,
        reason: str = "",
    ) -> float:
        if amount < 0:
            raise ValueError("release amount must be >= 0")
        return await self._apply(account, resource, period, amount, reason, charging=False)

    async def _apply(
        self,
        account: str,
        resource: Resource,
        period: str,
        amount: float,
        reason: str,
        *,
        charging: bool,
    ) -> float:
        res = str(resource)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO resource_totals (account, resource, period, used)
                VALUES ($1, $2, $3, 0)
                ON CONFLICT (account, resource, period) DO NOTHING
                """,
                account,
                res,
                period,
            )
            used = float(
                await conn.fetchval(
                    "SELECT used FROM resource_totals "
                    "WHERE account = $1 AND resource = $2 AND period = $3 FOR UPDATE",
                    account,
                    res,
                    period,
                )
            )
            if charging:
                ceiling = await conn.fetchval(
                    "SELECT limit_amount FROM resource_ceilings "
                    "WHERE account = $1 AND resource = $2",
                    account,
                    res,
                )
                if ceiling is not None and used + amount > float(ceiling):
                    raise CeilingExceeded(
                        account,
                        resource,
                        requested=amount,
                        used=used,
                        ceiling=float(ceiling),
                    )
                new_total = used + amount
            else:
                new_total = max(0.0, used - amount)

            await conn.execute(
                "UPDATE resource_totals SET used = $4 "
                "WHERE account = $1 AND resource = $2 AND period = $3",
                account,
                res,
                period,
                new_total,
            )
            await conn.execute(
                "INSERT INTO resource_events (account, resource, period, amount, reason) "
                "VALUES ($1, $2, $3, $4, $5)",
                account,
                res,
                period,
                amount if charging else -amount,
                reason,
            )
        return new_total

    async def used(self, account: str, resource: Resource, period: str) -> float:
        value = await self.pool.fetchval(
            "SELECT used FROM resource_totals "
            "WHERE account = $1 AND resource = $2 AND period = $3",
            account,
            str(resource),
            period,
        )
        return float(value) if value is not None else 0.0

    async def summary(self, account: str) -> list[tuple[Resource, str, float]]:
        rows = await self.pool.fetch(
            "SELECT resource, period, used FROM resource_totals "
            "WHERE account = $1 ORDER BY resource, period",
            account,
        )
        out: list[tuple[Resource, str, float]] = []
        for r in rows:
            try:
                resource = Resource(r["resource"])
            except ValueError:
                continue
            out.append((resource, r["period"], float(r["used"])))
        return out
