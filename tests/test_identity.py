"""IM-4.2 — real self-inventory + the first-boot awakening.

Offline: inventory introspects the real (discovered) registry/settings; the
awakening's memory sink + embedder are in-memory fakes.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np

from core.identity.bootstrap import ANCHOR, awaken
from core.identity.self_model import gather_inventory, render_self_context, version_str

_USER = uuid4()


class FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


class RecordingSemantic:
    def __init__(self) -> None:
        self.written: list[dict] = []

    async def content_exists(self, user_id, content: str) -> bool:
        return any(w["content"] == content for w in self.written)

    async def write(self, user_id, kind, content, embedding, *, source=None, importance=0.5):
        self.written.append(
            {"kind": kind, "content": content, "source": source, "importance": importance}
        )
        return uuid4()


# ── inventory ─────────────────────────────────────────────────────────


async def test_gather_inventory_reports_real_state():
    inv = await gather_inventory(
        memory_count=7, budget={"keys": [{"active": True}, {"active": False}]}
    )
    assert inv["agent"] == "Scrappy Singh"
    assert inv["operator"] == "Karnveer Singh"
    assert inv["version"] == version_str()
    # memory + mac connectors are always discovered (same as the console status test).
    assert {"memory", "mac"} <= set(inv["connectors"])
    assert inv["tool_count"] >= len(inv["connectors"])
    assert isinstance(inv["experts"], list)
    assert inv["memory_count"] == 7
    assert inv["key_pool"] == {"size": 2, "active": 1}
    assert inv["host"]


async def test_render_self_context_includes_version_and_memory_count():
    ctx = await render_self_context(experts=["ask_strategist"], memory_count=42)
    assert "Scrappy Singh" in ctx
    assert version_str() in ctx
    assert "42" in ctx
    assert "ask_strategist" in ctx


# ── awakening ─────────────────────────────────────────────────────────

_INV = {
    "model": "llama-3.3-70b",
    "provider": "openai",
    "host": "studio.local",
    "connectors": ["memory", "mac"],
    "experts": ["ask_strategist", "ask_memory_keeper"],
}


async def test_awaken_writes_birth_memories_and_grounds_first_words():
    sem = RecordingSemantic()
    r = await awaken(_INV, sem, FakeEmbedder(), _USER)

    assert r.born is True
    assert len(sem.written) == 3
    assert any(w["content"] == ANCHOR for w in sem.written)
    assert all(w["source"] == "birth" and w["kind"] == "reflection" for w in sem.written)
    assert all(w["importance"] >= 0.9 for w in sem.written)
    # First words are grounded in the real inventory, not generic.
    assert "Karnveer" in r.first_words
    assert "llama-3.3-70b" in r.first_words
    assert r.born_at in r.first_words


async def test_awaken_is_idempotent():
    sem = RecordingSemantic()
    uid = uuid4()
    await awaken(_INV, sem, FakeEmbedder(), uid)
    n = len(sem.written)

    again = await awaken(_INV, sem, FakeEmbedder(), uid)
    assert again.born is False
    assert len(sem.written) == n  # no new memories on re-run
    assert "Back online" in again.first_words


async def test_birth_memories_survive_to_answer_who_am_i():
    """The origin anchor persists, so 'who are you / when were you born' is
    answerable from memory later (it's a normal searchable memory)."""
    sem = RecordingSemantic()
    await awaken(_INV, sem, FakeEmbedder(), _USER)
    assert await sem.content_exists(_USER, ANCHOR)
