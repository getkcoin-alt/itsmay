"""IM-2.1 — local SQLite memory backend (the sovereign, no-server path).

Exercises the SQLite drop-ins end-to-end against a temp DB file: identity,
sessions, the rolling message window, vector search (cosine + importance
weighting + min-importance filter + use-count bump), dedup, browsing, and
delete. Plus the backend resolver's auto/explicit selection.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from core.memory import backend as backend_mod
from core.memory.sqlite_store import (
    SqliteEpisodicStore,
    SqliteSemanticStore,
    ensure_schema,
)


@pytest.fixture
def stores(tmp_path):
    path = ensure_schema(str(tmp_path / "mem.db"))
    return SqliteEpisodicStore(path), SqliteSemanticStore(path)


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


# ── schema / identity ─────────────────────────────────────────────


def test_ensure_schema_creates_file(tmp_path):
    p = ensure_schema(str(tmp_path / "nested" / "mem.db"))
    from pathlib import Path

    assert Path(p).exists()


async def test_get_or_create_user_is_idempotent(stores):
    epi, _ = stores
    a = await epi.get_or_create_user("karnveer")
    b = await epi.get_or_create_user("karnveer")
    c = await epi.get_or_create_user("someone_else")
    assert a == b
    assert a != c


# ── sessions + message window ─────────────────────────────────────


async def test_session_lifecycle_and_window(stores):
    epi, _ = stores
    uid = await epi.get_or_create_user("karnveer")
    sid = await epi.open_session(uid, channel="terminal")

    assert await epi.session_exists(sid, uid) is True
    import uuid

    assert await epi.session_exists(uuid.uuid4(), uid) is False

    await epi.append_message(sid, "user", "u1")
    await epi.append_message(sid, "assistant", "a1")
    await epi.append_message(sid, "tool", "t1")  # excluded from the window
    await epi.append_message(sid, "user", "u2")

    win = await epi.recent_window(sid, limit=10)
    assert [m.content for m in win] == ["u1", "a1", "u2"]  # oldest-first, no tool turn

    last_two = await epi.recent_window(sid, limit=2)
    assert [m.content for m in last_two] == ["a1", "u2"]


# ── semantic: write / search / rank ───────────────────────────────


async def test_search_ranks_by_cosine_similarity(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "alpha", _vec(1, 0, 0, 0))
    await sem.write(uid, "factual", "beta", _vec(0, 1, 0, 0))
    await sem.write(uid, "factual", "alpha-ish", _vec(0.9, 0.1, 0, 0))

    hits = await sem.search(uid, _vec(1, 0, 0, 0), k=2)
    assert [h.content for h in hits] == ["alpha", "alpha-ish"]
    assert hits[0].similarity > hits[1].similarity
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-5)


async def test_search_importance_weighting_breaks_ties(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "low", _vec(1, 0, 0, 0), importance=0.5)
    await sem.write(uid, "factual", "high", _vec(1, 0, 0, 0), importance=0.9)

    hits = await sem.search(uid, _vec(1, 0, 0, 0), k=2)
    # Same direction (sim==1) → importance decides: 0.9 outranks 0.5.
    assert hits[0].content == "high"


async def test_search_respects_min_importance(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "trivia", _vec(1, 0, 0, 0), importance=0.4)
    await sem.write(uid, "factual", "important", _vec(1, 0, 0, 0), importance=0.8)

    hits = await sem.search(uid, _vec(1, 0, 0, 0), k=5, min_importance=0.6)
    assert [h.content for h in hits] == ["important"]


async def test_search_bumps_use_count(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "alpha", _vec(1, 0, 0, 0))
    await sem.search(uid, _vec(1, 0, 0, 0), k=1)

    rows = await sem.list_recent(uid)
    assert rows[0].content == "alpha"
    assert rows[0].use_count == 1
    assert rows[0].last_used_at is not None


async def test_user_isolation(stores):
    epi, sem = stores
    a = await epi.get_or_create_user("a")
    b = await epi.get_or_create_user("b")
    await sem.write(a, "factual", "a-secret", _vec(1, 0, 0, 0))

    assert await sem.count(a) == 1
    assert await sem.count(b) == 0
    assert await sem.search(b, _vec(1, 0, 0, 0), k=5) == []


# ── management: dedup / list / delete ─────────────────────────────


async def test_content_exists_and_list_and_delete(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    rid = await sem.write(uid, "factual", "remember this", _vec(1, 0, 0, 0))

    assert await sem.content_exists(uid, "remember this") is True
    assert await sem.content_exists(uid, "never said") is False

    await sem.write(uid, "semantic", "second", _vec(0, 1, 0, 0))
    only_semantic = await sem.list_recent(uid, kind="semantic")
    assert [r.content for r in only_semantic] == ["second"]
    assert (await sem.list_recent(uid))[0].content == "second"  # newest-first

    assert await sem.delete(uid, rid) is True
    assert await sem.delete(uid, rid) is False  # already gone
    assert await sem.count(uid) == 1


# ── backend resolver ──────────────────────────────────────────────


def _fake_settings(memory_backend="auto", database_url=None):
    return types.SimpleNamespace(
        memory_backend=memory_backend,
        database_url=database_url,
        sqlite_path="~/.itsmay/memory.db",
        pg_dsn="postgresql://u:p@host:5432/db",
    )


@pytest.mark.parametrize(
    "memory_backend,database_url,expected",
    [
        ("auto", None, "sqlite"),  # local install → sqlite
        ("auto", "postgresql://x", "postgres"),  # Railway sets DATABASE_URL
        ("sqlite", "postgresql://x", "sqlite"),  # explicit override wins
        ("postgres", None, "postgres"),  # explicit override wins
    ],
)
def test_resolve_backend(monkeypatch, memory_backend, database_url, expected):
    monkeypatch.setattr(
        backend_mod, "get_settings", lambda: _fake_settings(memory_backend, database_url)
    )
    assert backend_mod.resolve_backend() == expected


def test_describe_backend_hides_postgres_credentials(monkeypatch):
    monkeypatch.setattr(
        backend_mod, "get_settings", lambda: _fake_settings("postgres", "postgresql://x")
    )
    d = backend_mod.describe_backend()
    assert d["backend"] == "postgres"
    assert "p@" not in d["location"] and "host:5432/db" in d["location"]
