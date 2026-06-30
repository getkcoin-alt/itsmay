"""Mini AI audio loop — device-only (needs a mic; the `[mini]` extra).

Capture (VAD) → local STT → voiceprint speaker-ID → engine (talk/observe) → local
Piper TTS → speaker. Fully offline. Kept thin: all the policy/memory logic lives in
the testable `CompanionEngine`; this file is just microphone + speaker plumbing.
"""

from __future__ import annotations

import asyncio
import io
import wave

import numpy as np
import sounddevice as sd

from apps.mac_agent.vad import SAMPLE_RATE, EnergyVADRecorder, VADRecorder
from core.companion.runtime import build_engine
from core.config import get_settings
from core.voice.tts_local import LocalTTS


def _record_utterance() -> np.ndarray | None:
    """One VAD-bounded utterance as int16 @ 16 kHz (webrtcvad, energy fallback)."""
    s = get_settings()
    try:
        return VADRecorder(
            aggressiveness=s.vad_aggressiveness, silence_ms=s.vad_silence_ms
        ).record()
    except Exception:
        return EnergyVADRecorder(silence_ms=s.vad_silence_ms).record()


def _record_fixed(seconds: float = 8.0, sr: int = SAMPLE_RATE) -> tuple[np.ndarray, int]:
    print("(listening… say a couple of sentences)")
    audio = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return audio.reshape(-1), sr


def _int16_to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.asarray(audio, dtype=np.int16).tobytes())
    return buf.getvalue()


def _play_pcm(pcm: bytes, sr: int) -> None:
    if not pcm:
        return
    sd.play(np.frombuffer(pcm, dtype=np.int16), sr)
    sd.wait()


async def _speak(tts: LocalTTS, text: str) -> None:
    chunks = [c async for c in tts.stream(text)]
    _play_pcm(b"".join(chunks), tts.sample_rate)


async def enroll_flow() -> None:
    from core.companion.persona import PERSONAS, persona_key, persona_title

    eng = build_engine()
    print("Let's set up a new friend.")
    wav, sr = _record_fixed(8.0)
    voiceprint = await eng.identifier.embed(wav, sr)
    name = (await asyncio.to_thread(input, "Their name (optional): ")).strip() or None
    nick = (await asyncio.to_thread(input, "What should they call me? ")).strip() or None
    choices = " / ".join(f"{k} ({t})" for k, (t, _f) in PERSONAS.items())
    raw = (await asyncio.to_thread(input, f"Personality [{choices}]: ")).strip()
    persona = persona_key(raw or get_settings().companion_persona)
    p = await eng.enroll(
        person_name=name, bot_nickname=nick, voiceprint=voiceprint, persona=persona
    )
    print(
        f"Done — {name or 'they'} can call me {nick or '(unnamed)'} "
        f"as their {persona_title(persona)}.  (id={p.id[:8]})"
    )


async def run_loop() -> None:
    from core.voice.stt_whisper import WhisperSTT

    eng = build_engine()
    if not await eng.profiles.all():
        print("No one's enrolled yet — run `mini enroll` first.")
        return

    stt = WhisperSTT(provider="local")
    tts = LocalTTS()
    if not tts.configured:
        print("(no Piper voice set — replies will be text-only. "
              "Set PIPER_VOICE_PATH to a .onnx voice for speech.)")
    print("Mini AI is listening.  (say your nickname to get my attention · Ctrl+C to stop)\n")

    while True:
        audio = _record_utterance()
        if audio is None or len(audio) == 0:
            continue
        result = await stt.transcribe(_int16_to_wav_bytes(audio, SAMPLE_RATE))
        text = (result.get("text") or "").strip()
        if not text:
            continue

        voiceprint = await eng.identifier.embed(
            np.asarray(audio, dtype=np.float32) / 32768.0, SAMPLE_RATE
        )
        _, profile = await eng.identify_speaker(voiceprint)
        if profile is None:
            print(f"  · (unrecognized voice) {text}")
            continue

        turn = await eng.handle_text(profile, text)
        await eng.profiles.blend_voiceprint(profile.id, voiceprint)  # sharpen over time

        tag = profile.person_name or profile.bot_nickname or profile.id[:8]
        if turn.spoke and turn.reply:
            print(f"  {tag}: {text}\n  {profile.bot_nickname or 'Mini'}: {turn.reply}")
            if tts.configured:
                try:
                    await _speak(tts, turn.reply)
                except Exception as e:  # never let a TTS hiccup kill the conversation
                    print(f"  (voice error, continuing text-only: {e})")
        else:
            print(f"  · ({turn.reason}) {tag}: {text}")
