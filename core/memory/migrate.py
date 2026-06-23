"""Tiny migration runner. Discovers SQL files under infra/migrations/ and applies
new ones in order, tracking what's been run in a `_migrations` table.

Called at API startup so a fresh Railway Postgres becomes usable on first boot
without any manual `psql` step.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from core.logging import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "infra" / "migrations"


async def run_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply pending SQL migrations in lexicographic filename order.

    Returns the list of migration filenames that were applied this run.
    Each migration runs inside its own transaction; failures abort that one
    migration and the loop, leaving the DB in a consistent state.
    """
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                name        TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already: set[str] = {
            row["name"] for row in await conn.fetch("SELECT name FROM _migrations")
        }

        if not MIGRATIONS_DIR.exists():
            log.warning("migrate.dir_missing", path=str(MIGRATIONS_DIR))
            return applied_now

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in already:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            log.info("migrate.applying", name=sql_file.name)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (name) VALUES ($1) "
                    "ON CONFLICT (name) DO NOTHING",
                    sql_file.name,
                )
            applied_now.append(sql_file.name)
            log.info("migrate.applied", name=sql_file.name)

    if applied_now:
        log.info("migrate.complete", applied=applied_now)
    else:
        log.info("migrate.up_to_date")
    return applied_now
