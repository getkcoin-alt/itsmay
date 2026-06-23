"""Episodic memory: sessions and turn-by-turn messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import numpy as np

from core.brain.llm import Message
from core.memory.db import get_pool

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class StoredMessage:
    id: UUID
    session_id: UUID
    role: Role
    content: str
    created_at: datetime


class EpisodicStore:
    async def get_or_create_user(self, handle: str) -> UUID:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (handle) VALUES ($1)
                ON CONFLICT (handle) DO UPDATE SET handle = EXCLUDED.handle
                RETURNING id
                """,
                handle,
            )
            return row["id"]

    async def open_session(self, user_id: UUID, channel: str = "api") -> UUID:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO sessions (user_id, channel) VALUES ($1, $2) RETURNING id",
                user_id,
                channel,
            )
            return row["id"]

    async def close_session(self, session_id: UUID, summary: str | None = None) -> None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET ended_at = now(), summary = $2 WHERE id = $1",
                session_id,
                summary,
            )

    async def append_message(
        self,
        session_id: UUID,
        role: Role,
        content: str,
        *,
        embedding: np.ndarray | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        latency_ms: int | None = None,
    ) -> UUID:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO messages
                    (session_id, role, content, embedding, tokens_in, tokens_out, latency_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                session_id,
                role,
                content,
                embedding,
                tokens_in,
                tokens_out,
                latency_ms,
            )
            return row["id"]

    async def recent_window(self, session_id: UUID, limit: int) -> list[Message]:
        """Most recent messages in this session, oldest-first, suitable for LLM context."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, created_at
                    FROM messages
                    WHERE session_id = $1 AND role IN ('user','assistant')
                    ORDER BY created_at DESC
                    LIMIT $2
                ) sub
                ORDER BY created_at ASC
                """,
                session_id,
                limit,
            )
            return [Message(role=r["role"], content=r["content"]) for r in rows]
