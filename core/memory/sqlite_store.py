"""Local SQLite memory backend — the sovereign, zero-server alternative to
Postgres + pgvector.

Same method surface as the Postgres `EpisodicStore` / `SemanticStore` (duck-typed
drop-ins, chosen at startup by `core.memory.backend`), but everything lives in a
single local file and vector search is brute-force cosine in numpy. For one
operator's machine — hundreds to low-thousands of memories — that's instant, and
it needs no server, no extension, no Docker. The API selects this automatically
when no `DATABASE_URL` is configured (`settings.memory_backend = "auto"`).

DB work runs in a worker thread via `asyncio.to_thread`, so the async signatures
match the Postgres stores and the event loop never blocks on disk I/O. Each call
opens its own short-lived connection (cheap on a local file, and safe across the
thread-pool threads `to_thread` uses).
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np

from core.brain.llm import Message
from core.logging import get_logger
from core.memory.episodic import Role
from core.memory.semantic import MemoryKind, MemoryRow, RetrievedMemory

log = get_logger(__name__)


# ── schema ────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    handle      TEXT UNIQUE NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL DEFAULT 'api',
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    summary     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, started_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content     TEXT NOT NULL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL,
    embedding   BLOB
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, created_at);
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    content      TEXT NOT NULL,
    source       TEXT,
    importance   REAL NOT NULL DEFAULT 0.5,
    decay_after  TEXT,
    embedding    BLOB,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    use_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_user_kind
    ON memories (user_id, kind, importance DESC);
"""


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema(path: str) -> str:
    """Create the DB file (and parent dir) and apply the schema. Idempotent.
    Returns the resolved absolute path."""
    resolved = str(Path(path).expanduser())
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(resolved)) as conn, conn:
        conn.executescript(_SCHEMA)
    log.info("sqlite.schema_ready", path=resolved)
    return resolved


# ── helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_blob(arr: np.ndarray | None) -> bytes | None:
    if arr is None:
        return None
    return np.asarray(arr, dtype=np.float32).tobytes()


def _from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def _cosine(q: np.ndarray, e: np.ndarray) -> float:
    denom = float(np.linalg.norm(q) * np.linalg.norm(e))
    if denom == 0.0:
        return 0.0
    return float(np.dot(q, e) / denom)


# ── episodic ──────────────────────────────────────────────────────────────


class SqliteEpisodicStore:
    """Drop-in for `EpisodicStore`, backed by a local SQLite file."""

    def __init__(self, path: str) -> None:
        self.path = path

    async def get_or_create_user(self, handle: str) -> UUID:
        return await asyncio.to_thread(self._get_or_create_user, handle)

    def _get_or_create_user(self, handle: str) -> UUID:
        with closing(_connect(self.path)) as conn, conn:
            row = conn.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone()
            if row:
                return UUID(row["id"])
            uid = uuid4()
            conn.execute(
                "INSERT INTO users (id, handle, created_at) VALUES (?, ?, ?)",
                (str(uid), handle, _now()),
            )
            return uid

    async def session_exists(self, session_id: UUID, user_id: UUID) -> bool:
        return await asyncio.to_thread(self._session_exists, session_id, user_id)

    def _session_exists(self, session_id: UUID, user_id: UUID) -> bool:
        with closing(_connect(self.path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?",
                (str(session_id), str(user_id)),
            ).fetchone()
            return row is not None

    async def open_session(self, user_id: UUID, channel: str = "api") -> UUID:
        return await asyncio.to_thread(self._open_session, user_id, channel)

    def _open_session(self, user_id: UUID, channel: str) -> UUID:
        sid = uuid4()
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, channel, started_at) VALUES (?, ?, ?, ?)",
                (str(sid), str(user_id), channel, _now()),
            )
        return sid

    async def close_session(self, session_id: UUID, summary: str | None = None) -> None:
        await asyncio.to_thread(self._close_session, session_id, summary)

    def _close_session(self, session_id: UUID, summary: str | None) -> None:
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
                (_now(), summary, str(session_id)),
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
        return await asyncio.to_thread(
            self._append_message,
            session_id,
            role,
            content,
            embedding,
            tokens_in,
            tokens_out,
            latency_ms,
        )

    def _append_message(
        self,
        session_id: UUID,
        role: Role,
        content: str,
        embedding: np.ndarray | None,
        tokens_in: int | None,
        tokens_out: int | None,
        latency_ms: int | None,
    ) -> UUID:
        mid = uuid4()
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, role, content, embedding, tokens_in, tokens_out,
                     latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(mid),
                    str(session_id),
                    role,
                    content,
                    _to_blob(embedding),
                    tokens_in,
                    tokens_out,
                    latency_ms,
                    _now(),
                ),
            )
        return mid

    async def recent_window(self, session_id: UUID, limit: int) -> list[Message]:
        return await asyncio.to_thread(self._recent_window, session_id, limit)

    def _recent_window(self, session_id: UUID, limit: int) -> list[Message]:
        with closing(_connect(self.path)) as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, rowid
                    FROM messages
                    WHERE session_id = ? AND role IN ('user','assistant')
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                ) sub
                ORDER BY created_at ASC, rowid ASC
                """,
                (str(session_id), limit),
            ).fetchall()
            return [Message(role=r["role"], content=r["content"]) for r in rows]


# ── semantic ──────────────────────────────────────────────────────────────


class SqliteSemanticStore:
    """Drop-in for `SemanticStore`, backed by a local SQLite file. Vector search
    is brute-force cosine in numpy — fine at single-operator scale."""

    def __init__(self, path: str) -> None:
        self.path = path

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
        return await asyncio.to_thread(
            self._write, user_id, kind, content, embedding, source, importance
        )

    def _write(
        self,
        user_id: UUID,
        kind: MemoryKind,
        content: str,
        embedding: np.ndarray,
        source: str | None,
        importance: float,
    ) -> UUID:
        rid = uuid4()
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO memories
                    (id, user_id, kind, content, source, importance, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(rid),
                    str(user_id),
                    kind,
                    content,
                    source,
                    importance,
                    _to_blob(embedding),
                    _now(),
                ),
            )
        return rid

    async def search(
        self,
        user_id: UUID,
        query_embedding: np.ndarray,
        k: int = 8,
        *,
        min_importance: float = 0.0,
    ) -> list[RetrievedMemory]:
        return await asyncio.to_thread(
            self._search, user_id, query_embedding, k, min_importance
        )

    def _search(
        self,
        user_id: UUID,
        query_embedding: np.ndarray,
        k: int,
        min_importance: float,
    ) -> list[RetrievedMemory]:
        q = np.asarray(query_embedding, dtype=np.float32)
        now = datetime.now(UTC)
        with closing(_connect(self.path)) as conn, conn:
            rows = conn.execute(
                """
                SELECT id, kind, content, importance, created_at, decay_after, embedding
                FROM memories
                WHERE user_id = ? AND embedding IS NOT NULL AND importance >= ?
                """,
                (str(user_id), min_importance),
            ).fetchall()

            scored: list[tuple[float, float, sqlite3.Row]] = []
            for r in rows:
                decay = _parse_ts(r["decay_after"])
                if decay is not None and decay <= now:
                    continue  # expired
                emb = _from_blob(r["embedding"])
                if emb is None or emb.shape != q.shape:
                    continue
                sim = _cosine(q, emb)
                rank = sim * (0.5 + 0.5 * float(r["importance"]))
                scored.append((rank, sim, r))

            scored.sort(key=lambda t: t[0], reverse=True)
            top = scored[:k]

            if top:
                ids = [t[2]["id"] for t in top]
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"""
                    UPDATE memories
                    SET last_used_at = ?, use_count = use_count + 1
                    WHERE id IN ({placeholders})
                    """,
                    [_now(), *ids],
                )

            return [
                RetrievedMemory(
                    id=UUID(r["id"]),
                    kind=r["kind"],
                    content=r["content"],
                    importance=float(r["importance"]),
                    similarity=sim,
                    created_at=_parse_ts(r["created_at"]) or now,
                )
                for (_rank, sim, r) in top
            ]

    async def content_exists(self, user_id: UUID, content: str) -> bool:
        return await asyncio.to_thread(self._content_exists, user_id, content)

    def _content_exists(self, user_id: UUID, content: str) -> bool:
        with closing(_connect(self.path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE user_id = ? AND content = ? LIMIT 1",
                (str(user_id), content),
            ).fetchone()
            return row is not None

    async def list_recent(
        self,
        user_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        kind: MemoryKind | None = None,
    ) -> list[MemoryRow]:
        return await asyncio.to_thread(self._list_recent, user_id, limit, offset, kind)

    def _list_recent(
        self, user_id: UUID, limit: int, offset: int, kind: MemoryKind | None
    ) -> list[MemoryRow]:
        with closing(_connect(self.path)) as conn:
            rows = conn.execute(
                """
                SELECT id, kind, content, source, importance,
                       created_at, last_used_at, use_count
                FROM memories
                WHERE user_id = ? AND (? IS NULL OR kind = ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (str(user_id), kind, kind, limit, offset),
            ).fetchall()
            return [
                MemoryRow(
                    id=UUID(r["id"]),
                    kind=r["kind"],
                    content=r["content"],
                    source=r["source"],
                    importance=float(r["importance"]),
                    created_at=_parse_ts(r["created_at"]) or datetime.now(UTC),
                    last_used_at=_parse_ts(r["last_used_at"]),
                    use_count=int(r["use_count"]),
                )
                for r in rows
            ]

    async def count(self, user_id: UUID) -> int:
        return await asyncio.to_thread(self._count, user_id)

    def _count(self, user_id: UUID) -> int:
        with closing(_connect(self.path)) as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM memories WHERE user_id = ?", (str(user_id),)
                ).fetchone()[0]
            )

    async def delete(self, user_id: UUID, memory_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete, user_id, memory_id)

    def _delete(self, user_id: UUID, memory_id: UUID) -> bool:
        with closing(_connect(self.path)) as conn, conn:
            cur = conn.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ?",
                (str(memory_id), str(user_id)),
            )
            return cur.rowcount > 0
