"""macOS `say` TTS — arg building + injected playback (no real `say` in CI)."""

from __future__ import annotations

from core.voice.tts_say import MacSayTTS


def test_say_args_with_voice_and_rate():
    tts = MacSayTTS(voice="Samantha", rate=180, exec_=lambda a: None)
    assert tts._args("hello") == ["say", "-v", "Samantha", "-r", "180", "hello"]


def test_say_args_default_system_voice():
    tts = MacSayTTS(voice="", rate=0, exec_=lambda a: None)
    assert tts._args("hi") == ["say", "hi"]


def test_say_configured_with_injected_exec():
    assert MacSayTTS(exec_=lambda a: None).configured is True


async def test_say_speak_invokes_exec_with_text():
    captured = {}

    async def fake_exec(argv):
        captured["argv"] = argv

    await MacSayTTS(voice="Karen", exec_=fake_exec).speak("how are you")
    assert captured["argv"] == ["say", "-v", "Karen", "how are you"]


async def test_say_speak_skips_empty():
    calls = {"n": 0}

    async def fake_exec(argv):
        calls["n"] += 1

    await MacSayTTS(exec_=fake_exec).speak("   ")
    assert calls["n"] == 0
