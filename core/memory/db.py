"""Async Postgres pool with pgvector registration.

Order matters: `register_vector` introspects the `vector` type at connection
init time, so the extension must exist in the database before any pooled
connection opens. On a brand-new Railway Postgres the extension is installed
but never enabled in our specific database — we bootstrap it here once before
the pool comes up.
"""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def _bootstrap_vector_extension(dsn: str) -> None:
    """Run `CREATE EXTENSION IF NOT EXISTS vector;` on a throwaway connection.

    Must run before the pool starts handing out connections, since the pool's
    init callback assumes the `vector` type already exists.
    """
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        log.info("pg.vector_extension_ready")
    finally:
        await conn.close()


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        s = get_settings()
        await _bootstrap_vector_extension(s.pg_dsn)
        _pool = await asyncpg.create_pool(
            dsn=s.pg_dsn,
            min_size=2,
            max_size=10,
            init=_init_conn,
        )
        log.info("pg.pool_created", dsn=s.pg_dsn.split("@")[-1])
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
