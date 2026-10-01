"""Persistence adapters for the durable goal ledger."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

import asyncpg

from core.goals.models import GoalState
from core.memory.sqlite_util import SqliteConnection, connect, restrict_file_perms


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_runtime_state (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class GoalStore(Protocol):
    async def load(self) -> GoalState: ...

    async def save(self, state: GoalState) -> None: ...


class PostgresGoalStore:
    """Single-row JSONB ledger on the same durable Postgres as Vault Zeta."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def load(self) -> GoalState:
        row = await self.pool.fetchrow(
            "SELECT state FROM goal_runtime_state WHERE id = 'primary'"
        )
        if row is None:
            return GoalState()
        raw = row["state"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        return GoalState.from_dict(raw)

    async def save(self, state: GoalState) -> None:
        payload = json.dumps(state.to_dict(), separators=(",", ":"), ensure_ascii=False)
        await self.pool.execute(
            """
            INSERT INTO goal_runtime_state (id, state, updated_at)
            VALUES ('primary', $1::jsonb, now())
            ON CONFLICT (id) DO UPDATE
            SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at
            """,
            payload,
        )


class SqliteGoalStore:
    """Local sovereign ledger stored beside the SQLite memory database."""

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

    async def load(self) -> GoalState:
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> GoalState:
        with self._db.cursor() as cur:
            row = cur.execute(
                "SELECT state FROM goal_runtime_state WHERE id = 'primary'"
            ).fetchone()
        return GoalState.from_dict(json.loads(row["state"])) if row else GoalState()

    async def save(self, state: GoalState) -> None:
        await asyncio.to_thread(self._save_sync, state)

    def _save_sync(self, state: GoalState) -> None:
        payload = json.dumps(state.to_dict(), separators=(",", ":"), ensure_ascii=False)
        with self._db.cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO goal_runtime_state (id, state, updated_at)
                VALUES ('primary', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE
                SET state = excluded.state, updated_at = CURRENT_TIMESTAMP
                """,
                (payload,),
            )

    def close(self) -> None:
        self._db.close()
