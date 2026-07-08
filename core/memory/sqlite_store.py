"""Local SQLite memory backend — the sovereign, zero-server alternative to
Postgres + pgvector.

Same method surface as the Postgres `EpisodicStore` / `SemanticStore` (duck-typed
drop-ins, chosen at startup by `core.memory.backend`), but everything lives in a
single local file and vector search is brute-force cosine in numpy. For one
operator's machine — hundreds to low-thousands of memories — that's instant, and
it needs no server, no extension, no Docker. The API selects this automatically
when no `DATABASE_URL` is configured (`settings.memory_backend = "auto"`).

DB work runs in a worker thread via `asyncio.to_thread`, so the async signatures
match the Postgres stores and the event loop never blocks on disk I/O. Each store
keeps ONE connection open (see `core.memory.sqlite_util.SqliteConnection`) and
serializes access with a lock, so the PRAGMAs are paid once, not per call, while
staying safe across the thread-pool threads `to_thread` uses.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np

from core.brain.llm import Message
from core.logging import get_logger
from core.memory.episodic import Role
from core.memory.semantic import MemoryKind, MemoryRow, RetrievedMemory
from core.memory.sqlite_util import (
    SqliteConnection,
    connect,
    from_blob,
    now_iso,
    parse_ts,
    restrict_file_perms,
    to_blob,
)

log = get_logger(__name__)

# Back-compat re-exports: `core.companion.profiles` (and possibly others) import
# these names from here. The canonical home is now `core.memory.sqlite_util`.
_connect = connect
_now = now_iso
_parse_ts = parse_ts
_to_blob = to_blob
_from_blob = from_blob


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


def ensure_schema(path: str) -> str:
    """Create the DB file (and parent dir) and apply the schema. Idempotent.
    Returns the resolved absolute path."""
    from pathlib import Path

    resolved = str(Path(path).expanduser())
    existed = Path(resolved).exists()
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(resolved)) as conn, conn:
        conn.executescript(_SCHEMA)
    if not existed:
        restrict_file_perms(resolved)  # private memories/voiceprints → owner-only
    log.info("sqlite.schema_ready", path=resolved)
    return resolved


# ── episodic ──────────────────────────────────────────────────────────────


class SqliteEpisodicStore:
    """Drop-in for `EpisodicStore`, backed by a local SQLite file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._db = SqliteConnection(path)

    async def get_or_create_user(self, handle: str) -> UUID:
        return await asyncio.to_thread(self._get_or_create_user, handle)

    def _get_or_create_user(self, handle: str) -> UUID:
        with self._db.cursor(write=True) as cur:
            row = cur.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone()
            if row:
                return UUID(row["id"])
            uid = uuid4()
            cur.execute(
                "INSERT INTO users (id, handle, created_at) VALUES (?, ?, ?)",
                (str(uid), handle, now_iso()),
            )
            return uid

    async def session_exists(self, session_id: UUID, user_id: UUID) -> bool:
        return await asyncio.to_thread(self._session_exists, session_id, user_id)

    def _session_exists(self, session_id: UUID, user_id: UUID) -> bool:
        with self._db.cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND user_id = ?",
                (str(session_id), str(user_id)),
            ).fetchone()
            return row is not None

    async def open_session(self, user_id: UUID, channel: str = "api") -> UUID:
        return await asyncio.to_thread(self._open_session, user_id, channel)

    def _open_session(self, user_id: UUID, channel: str) -> UUID:
        sid = uuid4()
        with self._db.cursor(write=True) as cur:
            cur.execute(
                "INSERT INTO sessions (id, user_id, channel, started_at) VALUES (?, ?, ?, ?)",
                (str(sid), str(user_id), channel, now_iso()),
            )
        return sid

    async def close_session(self, session_id: UUID, summary: str | None = None) -> None:
        await asyncio.to_thread(self._close_session, session_id, summary)

    def _close_session(self, session_id: UUID, summary: str | None) -> None:
        with self._db.cursor(write=True) as cur:
            cur.execute(
                "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
                (now_iso(), summary, str(session_id)),
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
        with self._db.cursor(write=True) as cur:
            cur.execute(
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
                    to_blob(embedding),
                    tokens_in,
                    tokens_out,
                    latency_ms,
                    now_iso(),
                ),
            )
        return mid

    async def recent_window(self, session_id: UUID, limit: int) -> list[Message]:
        return await asyncio.to_thread(self._recent_window, session_id, limit)

    def _recent_window(self, session_id: UUID, limit: int) -> list[Message]:
        with self._db.cursor() as cur:
            rows = cur.execute(
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

    async def recent_tool_traces(self, user_id: UUID, *, limit: int = 200) -> list[str]:
        """Raw JSON of recent tool-trace rows (role='tool') for the user,
        newest-first — the sovereign-path source for the workflow miner."""
        return await asyncio.to_thread(self._recent_tool_traces, user_id, limit)

    def _recent_tool_traces(self, user_id: UUID, limit: int) -> list[str]:
        with self._db.cursor() as cur:
            rows = cur.execute(
                """
                SELECT m.content
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE s.user_id = ? AND m.role = 'tool'
                ORDER BY m.created_at DESC, m.rowid DESC
                LIMIT ?
                """,
                (str(user_id), limit),
            ).fetchall()
            return [r["content"] for r in rows]


# ── semantic ──────────────────────────────────────────────────────────────


class SqliteSemanticStore:
    """Drop-in for `SemanticStore`, backed by a local SQLite file. Vector search
    is brute-force cosine in numpy — fine at single-operator scale."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._db = SqliteConnection(path)
        self._warned_dims: set[tuple[int, int]] = set()  # (stored, query) dims already logged

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
        with self._db.cursor(write=True) as cur:
            cur.execute(
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
                    to_blob(embedding),
                    now_iso(),
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
        kinds: set[str] | None = None,
    ) -> list[RetrievedMemory]:
        return await asyncio.to_thread(
            self._search, user_id, query_embedding, k, min_importance, kinds
        )

    def _search(
        self,
        user_id: UUID,
        query_embedding: np.ndarray,
        k: int,
        min_importance: float,
        kinds: set[str] | None = None,
    ) -> list[RetrievedMemory]:
        q = np.asarray(query_embedding, dtype=np.float32)
        now = datetime.now(UTC)
        with self._db.cursor(write=True) as cur:
            rows = cur.execute(
                """
                SELECT id, kind, content, importance, created_at, decay_after, embedding
                FROM memories
                WHERE user_id = ? AND embedding IS NOT NULL AND importance >= ?
                """,
                (str(user_id), min_importance),
            ).fetchall()

            # Gather non-expired, dimension-matching candidates, then score them
            # all at once with a single matrix-vector product (vectorized cosine)
            # instead of a Python cosine per row.
            candidates: list[sqlite3.Row] = []
            embeddings: list[np.ndarray] = []
            for r in rows:
                if kinds is not None and r["kind"] not in kinds:
                    continue  # caller wants only certain kinds (e.g. procedural)
                decay = parse_ts(r["decay_after"])
                if decay is not None and decay <= now:
                    continue  # expired
                emb = from_blob(r["embedding"])
                if emb is None:
                    continue
                if emb.shape != q.shape:
                    self._warn_dim_mismatch(emb.shape[0], q.shape[0])
                    continue  # model changed → stored dim no longer comparable
                candidates.append(r)
                embeddings.append(emb)

            if not candidates:
                return []

            mat = np.stack(embeddings)  # (n, dim)
            q_norm = float(np.linalg.norm(q))
            row_norms = np.linalg.norm(mat, axis=1)
            denom = row_norms * q_norm
            with np.errstate(divide="ignore", invalid="ignore"):
                sims = np.where(denom == 0.0, 0.0, (mat @ q) / denom)
            importances = np.array([float(r["importance"]) for r in candidates])
            ranks = sims * (0.5 + 0.5 * importances)

            # Descending by rank, stable tie-break on original order (matches the
            # previous Python `list.sort(reverse=True)` exactly).
            order = np.argsort(-ranks, kind="stable")[:k]

            if order.size:
                ids = [candidates[i]["id"] for i in order]
                placeholders = ",".join("?" * len(ids))
                cur.execute(
                    f"""
                    UPDATE memories
                    SET last_used_at = ?, use_count = use_count + 1
                    WHERE id IN ({placeholders})
                    """,
                    [now_iso(), *ids],
                )

            return [
                RetrievedMemory(
                    id=UUID(candidates[i]["id"]),
                    kind=candidates[i]["kind"],
                    content=candidates[i]["content"],
                    importance=float(candidates[i]["importance"]),
                    similarity=float(sims[i]),
                    created_at=parse_ts(candidates[i]["created_at"]) or now,
                )
                for i in order
            ]

    def _warn_dim_mismatch(self, stored_dim: int, query_dim: int) -> None:
        """Log once per (stored, query) dim pair when a memory's embedding no
        longer matches the query dim (usually an embedding-model change), so a
        silently-unsearchable memory is at least visible in the logs."""
        key = (stored_dim, query_dim)
        if key in self._warned_dims:
            return
        self._warned_dims.add(key)
        log.warning(
            "sqlite.memory_dim_mismatch",
            stored_dim=stored_dim,
            query_dim=query_dim,
            hint="embedding model likely changed; old-dim memories are skipped in search",
        )

    async def content_exists(self, user_id: UUID, content: str) -> bool:
        return await asyncio.to_thread(self._content_exists, user_id, content)

    def _content_exists(self, user_id: UUID, content: str) -> bool:
        with self._db.cursor() as cur:
            row = cur.execute(
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
        with self._db.cursor() as cur:
            rows = cur.execute(
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
                    created_at=parse_ts(r["created_at"]) or datetime.now(UTC),
                    last_used_at=parse_ts(r["last_used_at"]),
                    use_count=int(r["use_count"]),
                )
                for r in rows
            ]

    async def count(self, user_id: UUID) -> int:
        return await asyncio.to_thread(self._count, user_id)

    def _count(self, user_id: UUID) -> int:
        with self._db.cursor() as cur:
            return int(
                cur.execute(
                    "SELECT count(*) FROM memories WHERE user_id = ?", (str(user_id),)
                ).fetchone()[0]
            )

    async def delete(self, user_id: UUID, memory_id: UUID) -> bool:
        return await asyncio.to_thread(self._delete, user_id, memory_id)

    def _delete(self, user_id: UUID, memory_id: UUID) -> bool:
        with self._db.cursor(write=True) as cur:
            cur.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ?",
                (str(memory_id), str(user_id)),
            )
            return cur.rowcount > 0
