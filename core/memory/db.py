"""Async Postgres pool with pgvector registration."""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        s = get_settings()
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
