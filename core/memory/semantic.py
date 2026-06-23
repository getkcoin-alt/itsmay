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
