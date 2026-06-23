"""Semantic memory: long-lived facts, summaries, reflections, retrieved by similarity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import numpy as np

from core.memory.db import get_pool

MemoryKind = Literal["episodic", "semantic", "factual", "procedural", "reflection"]


@dataclass(slots=True)
class RetrievedMemory:
    id: UUID
    kind: MemoryKind
    content: str
    importance: float
    similarity: float
    created_at: datetime


@dataclass(slots=True)
class MemoryRow:
    """A stored memory without a similarity score — for browsing/management."""

    id: UUID
    kind: MemoryKind
    content: str
    source: str | None
    importance: float
    created_at: datetime
    last_used_at: datetime | None
    use_count: int


class SemanticStore:
    async def write(
        self,
        user_id: UUID,
        kind: MemoryKind,
        content: str,
        embedding: np.ndarray,
        *,
        source: str | None = None,
        importance: float = 0.5,
    ) -> UUID:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memories (user_id, kind, content, source, importance, embedding)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                user_id,
                kind,
                content,
                source,
                importance,
                embedding,
            )
            return row["id"]

    async def search(
        self,
        user_id: UUID,
        query_embedding: np.ndarray,
        k: int = 8,
        *,
        min_importance: float = 0.0,
    ) -> list[RetrievedMemory]:
        """Vector search with importance-weighted ranking."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kind, content, importance, created_at,
                       1 - (embedding <=> $2) AS similarity
                FROM memories
                WHERE user_id = $1
                  AND embedding IS NOT NULL
                  AND importance >= $4
                  AND (decay_after IS NULL OR decay_after > now())
                ORDER BY (1 - (embedding <=> $2)) * (0.5 + 0.5 * importance) DESC
                LIMIT $3
                """,
                user_id,
                query_embedding,
                k,
                min_importance,
            )
            # bump usage counters in background-ish fashion (single statement)
            if rows:
                ids = [r["id"] for r in rows]
                await conn.execute(
                    """
                    UPDATE memories
                    SET last_used_at = now(), use_count = use_count + 1
                    WHERE id = ANY($1::uuid[])
                    """,
                    ids,
                )
            return [
                RetrievedMemory(
                    id=r["id"],
                    kind=r["kind"],
                    content=r["content"],
                    importance=r["importance"],
                    similarity=float(r["similarity"]),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # ── management / browsing (no embedding involved) ────────────────
    async def list_recent(
        self,
        user_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        kind: MemoryKind | None = None,
    ) -> list[MemoryRow]:
        """Newest-first page of stored memories, for the console browser."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kind, content, source, importance,
                       created_at, last_used_at, use_count
                FROM memories
                WHERE user_id = $1
                  AND ($4::memory_kind IS NULL OR kind = $4)
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
                kind,
            )
            return [
                MemoryRow(
                    id=r["id"],
                    kind=r["kind"],
                    content=r["content"],
                    source=r["source"],
                    importance=r["importance"],
                    created_at=r["created_at"],
                    last_used_at=r["last_used_at"],
                    use_count=r["use_count"],
                )
                for r in rows
            ]

    async def count(self, user_id: UUID) -> int:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM memories WHERE user_id = $1", user_id
            )

    async def delete(self, user_id: UUID, memory_id: UUID) -> bool:
        """Delete one memory. Returns True if a row was removed."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE id = $1 AND user_id = $2",
                memory_id,
                user_id,
            )
            # asyncpg returns e.g. "DELETE 1"
            return result.rsplit(" ", 1)[-1] != "0"
