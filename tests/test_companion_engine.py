"""Mini AI engine, persona assembly, and local-TTS streaming.

The engine is driven with fakes (FakeLLM + a tiny deterministic embedder) over the
real SQLite profile/memory stores — no Ollama, torch, or audio — so the full
identify → gate → recall → generate → remember path runs in CI.
"""

from __future__ import annotations

import numpy as np

from core.companion.persona import build_companion_messages
from core.companion.profiles import ProfileStore, ensure_profile_schema
from core.companion.runtime import CompanionEngine
from core.companion.speaker_id import SpeakerIdentifier
from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore
from core.voice.tts_local import LocalTTS
from tests.fakes import FakeLLM


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


class _FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        h = (sum(ord(c) for c in text) % 100) / 100.0
        return np.array([1.0, h, 0.0, 0.0], dtype=np.float32)


def _engine(tmp_path, llm=None):
    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    return CompanionEngine(
        profiles=ProfileStore(path),
        semantic=SqliteSemanticStore(path),
        episodic=SqliteEpisodicStore(path),
        embedder=_FakeEmbedder(),
        llm=llm or FakeLLM([{"text": "haha hey!"}]),
        identifier=SpeakerIdentifier(threshold=0.75),
        active_window_s=30.0,
    )


# ── persona assembly (pure) ───────────────────────────────────────


def test_persona_injects_identity_and_memory():
    msgs = build_companion_messages(
        nickname="Pixel",
        person_name="Aman",
        retrieved_memories=["likes chai"],
        recent_messages=[],
        user_input="hi",
        persona="PERSONA-PROMPT",
    )
    sys = msgs[0].content
    assert msgs[0].role == "system"
    assert "PERSONA-PROMPT" in sys and "Pixel" in sys and "Aman" in sys and "likes chai" in sys
    assert msgs[-1].role == "user" and msgs[-1].content == "hi"


def test_persona_handles_unknown_name():
    msgs = build_companion_messages(
        nickname="Pixel",
        person_name=None,
        retrieved_memories=[],
        recent_messages=[],
        user_input="hi",
        persona="P",
    )
    assert "don't know their name" in msgs[0].content


# ── local TTS (injected synth) ────────────────────────────────────


async def test_local_tts_streams_injected_synth():
    tts = LocalTTS(synth=lambda text: [b"AB", b"CD", b""])
    assert tts.configured is True
    out = [c async for c in tts.stream("hi")]
    assert out == [b"AB", b"CD"]  # empty chunk dropped


def test_local_tts_unconfigured_without_voice_or_synth():
    assert LocalTTS(voice_path="").configured is False


# ── engine: identify / enroll ─────────────────────────────────────


async def test_single_profile_device_routes_any_voice(tmp_path):
    # One enrolled person → solo device → route everything to them, even a voice
    # far from their print (short clips make voiceprints unreliable).
    eng = _engine(tmp_path)
    p = await eng.enroll(person_name="Aman", bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    _, prof = await eng.identify_speaker(_vec(0, 0, 1, 0))
    assert prof is not None and prof.id == p.id


async def test_multi_profile_uses_voiceprint(tmp_path):
    # Two+ people → voiceprint gating kicks in.
    eng = _engine(tmp_path)
    a = await eng.enroll(person_name="A", bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    await eng.enroll(person_name="B", bot_nickname="Buddy", voiceprint=_vec(0, 1, 0, 0))
    _, prof = await eng.identify_speaker(_vec(0.95, 0.05, 0, 0))
    assert prof is not None and prof.id == a.id
    _, none = await eng.identify_speaker(_vec(0, 0, 1, 0))  # far from both → unknown
    assert none is None


# ── engine: talk vs observe, and memory always saved ──────────────


async def test_addressed_utterance_speaks_and_remembers(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "two robots walk into a bar…"}]))
    p = await eng.enroll(person_name="Aman", bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    turn = await eng.handle_text(p, "Pixel, tell me a joke", now=100.0)
    assert turn.spoke and turn.reason == "addressed"
    assert "robots" in turn.reply
    assert await eng.semantic.count(p.user_id) == 1  # remembered the user line


async def test_unaddressed_idle_observes_but_still_remembers(tmp_path):
    eng = _engine(tmp_path)
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(0, 1, 0, 0))
    turn = await eng.handle_text(p, "the weather is nice today", now=200.0)
    assert not turn.spoke and turn.reason == "observing"
    assert turn.reply == ""
    assert await eng.semantic.count(p.user_id) == 1  # observed silently, still saved


async def test_follow_up_within_window_speaks(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "hey you!"}, {"text": "i'm great!"}]))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    await eng.handle_text(p, "Pixel, hi", now=100.0)  # addressed → opens the window
    turn = await eng.handle_text(p, "how are you", now=110.0)  # 10s later, no nickname
    assert turn.spoke and turn.reason == "follow_up"


async def test_window_expires_back_to_observing(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "hi!"}]))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    await eng.handle_text(p, "Pixel, hi", now=100.0)
    turn = await eng.handle_text(p, "random thought", now=200.0)  # 100s later → idle again
    assert not turn.spoke and turn.reason == "observing"
