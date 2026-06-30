"""Companion voice selection (pure) + ElevenLabs PCM output.

No audio: `core.companion.voice` and the ElevenLabs PCM tweak are CI-testable;
the sounddevice playback/barge-in is validated on the device.
"""

from __future__ import annotations

import types

from core.companion import voice as voicemod
from core.companion.voice import select_index
from core.voice.tts_elevenlabs import ElevenLabsTTS, _sample_rate_from_format

_VOICES = [
    (None, "Piper", "piper"),
    (None, "ElevenLabs", "elevenlabs"),
    (None, "macOS say", "say"),
]


# ── select_index ──────────────────────────────────────────────────


def test_select_index_explicit_pref():
    assert select_index(_VOICES, "elevenlabs") == 1
    assert select_index(_VOICES, "say") == 2
    assert select_index(_VOICES, "piper") == 0


def test_select_index_auto_prefers_local():
    assert select_index(_VOICES, "auto") == 0  # piper is local
    assert select_index([(None, "ElevenLabs", "elevenlabs"), (None, "Say", "say")], "auto") == 1


def test_select_index_unknown_pref_falls_back_to_local():
    voices = [(None, "ElevenLabs", "elevenlabs"), (None, "Piper", "piper")]
    assert select_index(voices, "nonsense") == 1  # → first local


def test_select_index_empty():
    assert select_index([], "piper") == 0


def test_available_voices_adds_elevenlabs_when_key_present(monkeypatch):
    fake = types.SimpleNamespace(
        elevenlabs_api_key="sk-test",
        elevenlabs_voice_id="v",
        elevenlabs_model_id="m",
        elevenlabs_output_format="pcm_24000",
        piper_voice_path="",  # not configured → piper excluded
        piper_length_scale=1.0,
    )
    monkeypatch.setattr(voicemod, "get_settings", lambda: fake)
    keys = [k for _, _, k in voicemod.available_voices()]
    assert "elevenlabs" in keys


# ── ElevenLabs PCM output ─────────────────────────────────────────


def test_sample_rate_parsing():
    assert _sample_rate_from_format("pcm_24000") == 24000
    assert _sample_rate_from_format("pcm_16000") == 16000
    assert _sample_rate_from_format("mp3_44100_128") == 44100
    assert _sample_rate_from_format("weird") == 44100


def test_elevenlabs_exposes_sample_rate_from_format():
    assert ElevenLabsTTS(api_key="x", output_format="pcm_24000").sample_rate == 24000
    assert ElevenLabsTTS(api_key="x", output_format="mp3_44100_128").sample_rate == 44100
