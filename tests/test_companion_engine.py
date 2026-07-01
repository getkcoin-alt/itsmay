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


# ── mode override: press-O observe vs talk-to-everything ──────────


async def test_mode_observe_never_speaks_but_remembers(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "hi"}]))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    turn = await eng.handle_text(p, "Pixel, hello there", mode="observe", now=100.0)
    assert not turn.spoke and turn.reason == "observe" and turn.reply == ""
    assert await eng.semantic.count(p.user_id) == 1  # still remembered while observing


async def test_mode_talk_replies_without_a_nickname(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "sure thing"}]))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    turn = await eng.handle_text(p, "what's the weather", mode="talk", now=100.0)
    assert turn.spoke and turn.reason == "talk" and turn.reply == "sure thing"


async def test_mode_auto_still_uses_the_gate(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "hi"}]))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    turn = await eng.handle_text(p, "random thought", mode="auto", now=100.0)
    assert not turn.spoke and turn.reason == "observing"  # unaddressed + idle


# ── session guard (P0-2) ──────────────────────────────────────────


class _CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str) -> np.ndarray:
        self.calls += 1
        h = (sum(ord(c) for c in text) % 100) / 100.0
        return np.array([1.0, h, 0.0, 0.0], dtype=np.float32)


async def test_concurrent_utterances_open_one_session(tmp_path):
    """Two near-simultaneous utterances for the SAME profile must share one
    session — the per-profile lock closes the check-then-open race."""
    import asyncio

    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    episodic = SqliteEpisodicStore(path)

    opens = 0
    real_open = episodic.open_session

    async def slow_open(user_id, channel="api"):
        nonlocal opens
        opens += 1
        await asyncio.sleep(0.02)  # widen the race window
        return await real_open(user_id, channel=channel)

    episodic.open_session = slow_open  # type: ignore[method-assign]
    eng = CompanionEngine(
        profiles=ProfileStore(path),
        semantic=SqliteSemanticStore(path),
        episodic=episodic,
        embedder=_FakeEmbedder(),
        llm=FakeLLM([{"text": "a"}, {"text": "b"}]),
        identifier=SpeakerIdentifier(threshold=0.75),
    )
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))

    await asyncio.gather(
        eng.handle_text(p, "Pixel, one", now=100.0),
        eng.handle_text(p, "Pixel, two", now=100.0),
    )
    assert opens == 1  # a single session despite two concurrent calls
    assert len(eng._sessions) == 1


async def test_tracked_profile_maps_are_bounded(tmp_path):
    """`_sessions`/`_last_spoke` can't grow without limit — old entries evict."""
    from core.companion import runtime as rt

    eng = _engine(tmp_path)
    eng.profiles  # noqa: B018 (ensure schema created via fixture)
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))

    # Simulate many distinct profiles cycling through the in-memory maps.
    for i in range(rt._MAX_TRACKED_PROFILES + 25):
        eng._sessions[f"pid-{i}"] = p.user_id
        eng._remember_spoke(f"pid-{i}", float(i))
        # keep _sessions bounded the same way the real open path does
        while len(eng._sessions) > rt._MAX_TRACKED_PROFILES:
            eng._sessions.popitem(last=False)

    assert len(eng._sessions) <= rt._MAX_TRACKED_PROFILES
    assert len(eng._last_spoke) <= rt._MAX_TRACKED_PROFILES


# ── no wasted embed on the assistant reply (P1-6) ─────────────────


async def test_assistant_reply_is_not_embedded(tmp_path):
    """Speaking embeds only the user's line (recall needs it); the assistant turn
    is stored without an embedding since nothing vector-searches replies."""
    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    emb = _CountingEmbedder()
    eng = CompanionEngine(
        profiles=ProfileStore(path),
        semantic=SqliteSemanticStore(path),
        episodic=SqliteEpisodicStore(path),
        embedder=emb,
        llm=FakeLLM([{"text": "haha sure"}]),
        identifier=SpeakerIdentifier(threshold=0.75),
    )
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))
    turn = await eng.handle_text(p, "Pixel, tell me a joke", now=100.0)
    assert turn.spoke

    # Only ONE embed happened this turn: the user utterance (used for recall +
    # semantic write). No second embed for the reply.
    assert emb.calls == 1

    # And the stored assistant message carries no embedding blob.
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT role, embedding FROM messages ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    by_role = {r[0]: r[1] for r in rows}
    assert by_role["user"] is not None
    assert by_role["assistant"] is None


# ── observation guard: don't flood the store (P2-9) ───────────────


async def test_short_and_duplicate_observations_are_not_stored(tmp_path):
    eng = _engine(tmp_path, llm=FakeLLM([{"text": "x"}] * 5))
    p = await eng.enroll(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0))

    await eng.handle_text(p, "yes", mode="observe", now=100.0)  # too short → skipped
    assert await eng.semantic.count(p.user_id) == 0

    await eng.handle_text(p, "I really like long walks", mode="observe", now=101.0)
    assert await eng.semantic.count(p.user_id) == 1  # substantive → stored

    await eng.handle_text(p, "I really like long walks", mode="observe", now=102.0)
    assert await eng.semantic.count(p.user_id) == 1  # exact duplicate → not stored again


# ── unknown-voice prompt throttling (missing piece, finding 10) ───


def test_unknown_voice_prompter_throttles():
    from core.companion.runtime import UnknownVoicePrompter

    pr = UnknownVoicePrompter(cooldown_s=30.0)
    first = pr.prompt(now=100.0)
    assert first is not None and "mini enroll" in first
    assert pr.prompt(now=110.0) is None  # within cooldown → quiet
    assert pr.prompt(now=131.0) is not None  # cooldown elapsed → hint again
