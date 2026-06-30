"""Mini AI foundations — profiles, voiceprint matching, and the when-to-talk gate.

All CI-runnable: the heavy bits (torch voice encoder, Ollama, audio) are isolated
behind injectable seams, so these exercise the pure logic + the SQLite profile
store with no models.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.companion.gate import (
    GateDecision,
    detect_address,
    is_active_window,
    should_respond,
)
from core.companion.profiles import ProfileStore, ensure_profile_schema
from core.companion.speaker_id import Match, best_match
from core.memory.sqlite_store import SqliteSemanticStore


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


# ── profiles ──────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    return ProfileStore(path)


async def test_create_person_gets_namespace_and_profile(store):
    p = await store.create_person(
        person_name="Aman", bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0)
    )
    assert p.bot_nickname == "Pixel"
    assert p.person_name == "Aman"
    assert p.user_id is not None  # a fresh memory namespace
    again = await store.get(p.id)
    assert again is not None and again.bot_nickname == "Pixel"
    assert np.allclose(again.voiceprint, _vec(1, 0, 0, 0))
    assert [x.id for x in await store.all()] == [p.id]


async def test_two_people_get_separate_namespaces(store):
    a = await store.create_person(person_name="A", bot_nickname="Pixel", voiceprint=_vec(1, 0))
    b = await store.create_person(person_name="B", bot_nickname="Buddy", voiceprint=_vec(0, 1))
    assert a.user_id != b.user_id  # isolated memories per person


async def test_blend_voiceprint_running_average(store):
    p = await store.create_person(
        person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0)
    )
    await store.blend_voiceprint(p.id, _vec(0, 1, 0, 0))
    got = await store.get(p.id)
    assert got.sample_count == 2
    assert np.allclose(got.voiceprint, _vec(0.5, 0.5, 0, 0))  # (v1*1 + v2)/2


async def test_set_nickname_and_touch(store):
    p = await store.create_person(person_name=None, bot_nickname="Pixel", voiceprint=_vec(1, 0))
    await store.set_nickname(p.id, "Sparky")
    assert (await store.get(p.id)).bot_nickname == "Sparky"


async def test_forget_cascades_memories(store, tmp_path):
    p = await store.create_person(
        person_name="A", bot_nickname="Pixel", voiceprint=_vec(1, 0, 0, 0)
    )
    sem = SqliteSemanticStore(store.path)
    await sem.write(p.user_id, "factual", "likes chai", _vec(1, 0, 0, 0))
    assert await sem.count(p.user_id) == 1

    assert await store.forget(p.id) is True
    assert await sem.count(p.user_id) == 0  # user row gone → memories cascade
    assert await store.get(p.id) is None
    assert await store.forget(p.id) is False  # already gone


# ── speaker-id matching (pure) ────────────────────────────────────


def test_best_match_picks_nearest_above_threshold():
    profiles = [("a", _vec(1, 0, 0, 0)), ("b", _vec(0, 1, 0, 0))]
    m = best_match(_vec(0.9, 0.1, 0, 0), profiles, threshold=0.75)
    assert m.profile_id == "a" and m.score > 0.9


def test_best_match_below_threshold_is_new_person():
    profiles = [("a", _vec(1, 0, 0, 0))]
    m = best_match(_vec(0, 1, 0, 0), profiles, threshold=0.5)
    assert m.profile_id is None  # orthogonal → not a match


def test_best_match_empty_and_none_voiceprints():
    assert best_match(_vec(1, 0), [], threshold=0.5) == Match(None, 0.0)
    m = best_match(_vec(1, 0), [("a", None), ("b", _vec(1, 0))], threshold=0.5)
    assert m.profile_id == "b"  # None voiceprint skipped


# ── when-to-talk gate (pure) ──────────────────────────────────────


def test_gate_addressed_by_nickname_strips_address():
    d = should_respond("Pixel, what's the time?", nickname="Pixel", in_active_chat=False)
    assert d.speak and d.reason == "addressed"
    assert d.cleaned_text == "what's the time?"


def test_gate_address_allows_wake_word_and_casing():
    d = should_respond("hey pixel how are you", nickname="Pixel", in_active_chat=False)
    assert d.speak and d.reason == "addressed"
    assert d.cleaned_text == "how are you"


def test_gate_nickname_midsentence_is_not_an_address():
    d = should_respond("I saw Pixel yesterday", nickname="Pixel", in_active_chat=False)
    assert not d.speak and d.reason == "observing"


def test_gate_follow_up_in_active_chat_speaks():
    d = should_respond("and what about tomorrow", nickname="Pixel", in_active_chat=True)
    assert d.speak and d.reason == "follow_up"


def test_gate_unaddressed_and_idle_observes():
    d = should_respond("what time is it", nickname="Pixel", in_active_chat=False)
    assert not d.speak and d.reason == "observing"


def test_gate_empty_is_no_speech():
    assert should_respond("   ", nickname="Pixel", in_active_chat=True) == GateDecision(
        False, "no_speech", ""
    )


def test_detect_address_name_only_falls_back_to_original():
    addressed, cleaned = detect_address("Pixel?", "Pixel")
    assert addressed and cleaned == "Pixel?"


def test_is_active_window():
    assert is_active_window(100.0, 110.0, 30.0) is True
    assert is_active_window(100.0, 200.0, 30.0) is False
    assert is_active_window(None, 10.0, 30.0) is False


# ── mini app: profile resolution by id prefix ─────────────────────


async def test_mini_resolve_by_full_or_prefix_id(tmp_path):
    from apps.mini import _resolve

    path = ensure_profile_schema(str(tmp_path / "mini.db"))
    s = ProfileStore(path)
    p = await s.create_person(person_name="A", bot_nickname="Pixel", voiceprint=None)
    assert (await _resolve(s, p.id)).id == p.id
    assert (await _resolve(s, p.id[:8])).id == p.id  # short prefix from `mini profiles`
    assert await _resolve(s, "nomatch") is None
