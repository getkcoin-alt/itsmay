"""Selectable personalities — the registry, per-profile storage, and that each
person's chosen persona actually reaches the model.
"""

from __future__ import annotations

import numpy as np

from core.companion.persona import (
    DEFAULT_PERSONA,
    PERSONAS,
    load_persona,
    persona_key,
    persona_title,
)
from core.companion.profiles import ProfileStore, ensure_profile_schema
from core.companion.runtime import CompanionEngine
from core.companion.speaker_id import SpeakerIdentifier
from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore
from tests.fakes import FakeLLM


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


class _FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        return np.array([1.0, len(text) % 7 / 7.0, 0.0, 0.0], dtype=np.float32)


def _engine(tmp_path, llm):
    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    return CompanionEngine(
        profiles=ProfileStore(path),
        semantic=SqliteSemanticStore(path),
        episodic=SqliteEpisodicStore(path),
        embedder=_FakeEmbedder(),
        llm=llm,
        identifier=SpeakerIdentifier(threshold=0.75),
    )


# ── registry ──────────────────────────────────────────────────────


def test_two_personas_registered():
    assert {"friend", "mentor"} <= set(PERSONAS)
    assert DEFAULT_PERSONA == "friend"


def test_persona_key_normalizes_unknown_to_default():
    assert persona_key("mentor") == "mentor"
    assert persona_key("MENTOR ") == "mentor"
    assert persona_key("nonsense") == "friend"
    assert persona_key(None) == "friend"
    assert persona_title("mentor") == "Mentor"


def test_load_persona_returns_distinct_prompts():
    friend = load_persona("friend")
    mentor = load_persona("mentor")
    assert "best friend" in friend.lower()
    assert "calm confidence" in mentor.lower()
    assert "calm confidence" not in friend.lower()
    # Unknown / None fall back to the friend default.
    assert load_persona("bogus") == friend
    assert load_persona(None) == friend


# ── per-profile storage ───────────────────────────────────────────


async def test_profile_stores_and_updates_persona(tmp_path):
    store = ProfileStore(ensure_profile_schema(str(tmp_path / "mini.db")))
    p = await store.create_person(
        person_name=None, bot_nickname="Coach", voiceprint=_vec(1, 0), persona="mentor"
    )
    assert (await store.get(p.id)).persona == "mentor"
    await store.set_persona(p.id, "friend")
    assert (await store.get(p.id)).persona == "friend"


# ── the chosen persona reaches the model ──────────────────────────


async def test_mentor_persona_flows_to_the_model(tmp_path):
    llm = FakeLLM([{"text": "noted."}])
    eng = _engine(tmp_path, llm)
    p = await eng.enroll(
        person_name=None, bot_nickname="Coach", voiceprint=_vec(1, 0, 0, 0), persona="mentor"
    )
    await eng.handle_text(p, "Coach, am I on track?", now=100.0)
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "calm confidence" in system_prompt.lower()  # mentor reached the LLM
    assert "best friend" not in system_prompt.lower()


async def test_no_persona_defaults_to_friend_at_the_model(tmp_path):
    llm = FakeLLM([{"text": "haha hey"}])
    eng = _engine(tmp_path, llm)
    p = await eng.enroll(
        person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0)
    )  # no persona → None
    await eng.handle_text(p, "Pixel, hi", now=100.0)
    assert "best friend" in llm.calls[0]["messages"][0]["content"].lower()
