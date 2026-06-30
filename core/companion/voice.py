"""Companion voice selection — which TTS engine speaks.

Pure + CI-testable (no audio deps): builds the list of usable engines and picks
the starting one from `companion_voice`. The actual playback/barge-in lives in
`apps/mini/loop.py` (sounddevice, device-only).

Order is piper → elevenlabs → say. "auto" prefers a LOCAL voice (sovereign
default); ElevenLabs is used only when explicitly chosen (or cycled to with V).
"""

from __future__ import annotations

from typing import Any

from core.config import get_settings
from core.voice.tts_elevenlabs import ElevenLabsTTS
from core.voice.tts_local import LocalTTS
from core.voice.tts_say import MacSayTTS

# (tts, human label, key)
Voice = tuple[Any, str, str]


def available_voices() -> list[Voice]:
    """Every voice engine usable right now, ordered piper → elevenlabs → say."""
    s = get_settings()
    out: list[Voice] = []
    piper = LocalTTS()
    if piper.configured:
        out.append((piper, "Piper", "piper"))
    if s.elevenlabs_api_key:
        out.append((ElevenLabsTTS(output_format="pcm_24000"), "ElevenLabs", "elevenlabs"))
    say = MacSayTTS()
    if say.configured:
        out.append((say, "macOS say", "say"))
    return out


def select_index(voices: list[Voice], pref: str | None) -> int:
    """Where to start: an explicit `pref` (piper/say/elevenlabs) by key, else
    'auto' → the first LOCAL voice (piper/say), else 0."""
    if not voices:
        return 0
    pref = (pref or "auto").lower()
    if pref != "auto":
        for i, (_, _, key) in enumerate(voices):
            if key == pref:
                return i
    for i, (_, _, key) in enumerate(voices):
        if key in ("piper", "say"):
            return i
    return 0
