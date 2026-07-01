"""Shared SQLite plumbing for the local (sovereign) memory + companion stores.

Public home for the small helpers the SQLite backend and the companion profile
store both need — connection setup, timestamp formatting, and numpy<->BLOB
conversion — so importers don't reach into a sibling module's privates.

`SqliteConnection` keeps **one** connection open per store instance instead of
opening a fresh one on every call. On a local file that used to mean re-running
the PRAGMAs (WAL, foreign_keys, busy_timeout) 6-7 times per utterance. The single
connection is opened with `check_same_thread=False` and every use is serialized by
a `threading.Lock`, so it stays safe across the worker threads `asyncio.to_thread`
hands work to (SQLite connections aren't thread-safe for concurrent use).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

# WAL + a real busy_timeout so concurrent writers wait for the lock instead of
# raising `OperationalError: database is locked` (SQLite's default timeout is 0).
_BUSY_TIMEOUT_MS = 5000


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")


def connect(path: str) -> sqlite3.Connection:
    """Open a single-thread connection with the standard PRAGMAs applied.

    Used for one-shot work (schema creation). Long-lived stores should use
    `SqliteConnection` so the PRAGMAs are paid once, not per call.
    """
    conn = sqlite3.connect(path)
    _apply_pragmas(conn)
    return conn


class SqliteConnection:
    """One reused connection per store, serialized by a lock.

    Every DB method runs its body inside `with self.cursor() as cur:`. The lock
    makes concurrent access from `asyncio.to_thread` worker threads safe, and
    `check_same_thread=False` lets those (different) threads use the one handle.
    Writes are wrapped in a transaction that commits on success / rolls back on
    error — mirroring the previous `with closing(_connect(path)) as conn, conn:`.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            _apply_pragmas(self._conn)
        return self._conn

    @contextmanager
    def cursor(self, *, write: bool = False) -> Iterator[sqlite3.Cursor]:
        """A short critical section holding the connection lock.

        `write=True` commits at the end (rolling back on exception) so multi-row
        writes stay atomic; reads don't need the commit.
        """
        with self._lock:
            conn = self._ensure()
            cur = conn.cursor()
            try:
                yield cur
                if write:
                    conn.commit()
            except Exception:
                if write:
                    conn.rollback()
                raise
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


# ── helpers (shared) ──────────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_blob(arr: np.ndarray | None) -> bytes | None:
    if arr is None:
        return None
    return np.asarray(arr, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def restrict_file_perms(path: str) -> None:
    """Best-effort: make a freshly-created DB file owner-only (0600).

    Memories and voiceprints are private to the operator; on a shared box this
    keeps other users from reading the file. No-op / silently ignored where
    chmod isn't meaningful (e.g. Windows), and never raises.
    """
    try:
        p = Path(path)
        if p.exists():
            os.chmod(p, 0o600)
    except OSError:
        pass
