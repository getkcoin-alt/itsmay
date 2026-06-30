"""Mini AI audio loop — device-only (needs a mic; the `[mini]` extra).

Capture (VAD) → local STT → voiceprint speaker-ID → engine → voice (local say/Piper
or expressive ElevenLabs) → speaker. The brain + memory stay local regardless of
voice. Kept thin: all policy/memory logic lives in the testable `CompanionEngine`
and voice *selection* in `core.companion.voice`; this file is mic/speaker plumbing.

Live controls (single keypress, no Enter):
  O — toggle observe (just listen + remember)  ·  V — cycle the voice  ·  Ctrl-C — quit
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import threading
import wave
from dataclasses import dataclass, field

import numpy as np
import sounddevice as sd

from apps.mac_agent.vad import SAMPLE_RATE, EnergyVADRecorder, VADRecorder
from core.companion.runtime import build_engine
from core.companion.voice import available_voices, select_index
from core.config import get_settings
from core.voice.tts_say import MacSayTTS


# ── live controls (keyboard) ──────────────────────────────────────
@dataclass
class _Controls:
    observe: threading.Event = field(default_factory=threading.Event)
    cycle_voice: threading.Event = field(default_factory=threading.Event)


def _start_key_watcher(controls: _Controls):
    """Daemon thread reading single keys (O / V). Returns a restore() to reset the
    terminal on exit. No-op when stdin isn't a TTY (falls back to line input)."""
    if not sys.stdin.isatty():
        def _lines() -> None:
            for line in sys.stdin:
                _dispatch_key(line.strip()[:1].lower(), controls)
        threading.Thread(target=_lines, daemon=True).start()
        return lambda: None

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)  # single keys, but keep Ctrl-C (ISIG) working

    def _keys() -> None:
        with contextlib.suppress(Exception):
            while True:
                ch = sys.stdin.read(1)
                if not ch:
                    return
                _dispatch_key(ch.lower(), controls)

    threading.Thread(target=_keys, daemon=True).start()
    return lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _dispatch_key(ch: str, controls: _Controls) -> None:
    if ch == "o":
        if controls.observe.is_set():
            controls.observe.clear()
            print("\n💬 talking — press O to observe", flush=True)
        else:
            controls.observe.set()
            print("\n👀 observing — press O to talk", flush=True)
    elif ch == "v":
        controls.cycle_voice.set()


# ── capture / playback ────────────────────────────────────────────
def _record_utterance() -> np.ndarray | None:
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


def _maybe_barge_in():
    """A BargeInMonitor listening for the user interrupting the bot, or None."""
    try:
        from apps.mac_agent.vad import BargeInMonitor

        m = BargeInMonitor(aggressiveness=get_settings().vad_aggressiveness)
        m.start()
        return m
    except Exception:
        return None


async def _speak(tts, text: str, *, barge_in: bool = True) -> None:
    """Speak `text`, interruptible: if the user starts talking, stop immediately."""
    if isinstance(tts, MacSayTTS):
        await _speak_say(tts, text, barge_in)
    else:
        await _speak_pcm(tts, text, barge_in)


async def _speak_pcm(tts, text: str, barge_in: bool) -> None:
    pcm = b"".join([c async for c in tts.stream(text)])
    pcm = pcm[: len(pcm) - len(pcm) % 2]  # whole int16 samples
    if not pcm:
        return
    audio = np.frombuffer(pcm, dtype=np.int16)
    monitor = _maybe_barge_in() if barge_in else None
    try:
        sd.play(audio, tts.sample_rate)
        while sd.get_stream().active:
            if monitor is not None and monitor.detected.is_set():
                sd.stop()
                print("⚡ [interrupted]", flush=True)
                break
            await asyncio.sleep(0.08)
    finally:
        if monitor is not None:
            monitor.stop()


async def _speak_say(tts: MacSayTTS, text: str, barge_in: bool) -> None:
    monitor = _maybe_barge_in() if barge_in else None
    proc = await asyncio.create_subprocess_exec(*tts._args(text))
    try:
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.1)
                break
            except TimeoutError:
                if monitor is not None and monitor.detected.is_set():
                    with contextlib.suppress(ProcessLookupError):
                        proc.terminate()
                    print("⚡ [interrupted]", flush=True)
                    break
    finally:
        if monitor is not None:
            monitor.stop()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        await proc.wait()


# ── enrollment ────────────────────────────────────────────────────
_ENROLL_LINES = [
    "Hi, this is my voice, and I'm setting up my own AI companion.",
    "I like clear mornings, good music, and getting real things done.",
    "Learn how I sound — my rhythm, my pace, the way I pause.",
    "From now on, when I call your name, you'll know it's me.",
]
_ENROLL_SECONDS = 16.0


async def enroll_flow() -> None:
    from core.companion.persona import PERSONAS, persona_key, persona_title

    eng = build_engine()
    print("\nLet's learn your voice so I can recognise you later.\n")
    print(f"When you're ready, I'll listen for about {int(_ENROLL_SECONDS)} seconds.")
    print("Read these lines out loud, naturally — no rush:\n")
    for line in _ENROLL_LINES:
        print(f"   “{line}”")
    await asyncio.to_thread(input, "\n[ press Enter to start recording ] ")

    wav, sr = _record_fixed(_ENROLL_SECONDS)
    voiceprint = await eng.identifier.embed(wav, sr)
    print("Got it. ✓\n")

    name = (await asyncio.to_thread(input, "Your name (optional): ")).strip() or None
    nick = (await asyncio.to_thread(input, "What should you call me? ")).strip() or None
    choices = " / ".join(f"{k} ({t})" for k, (t, _f) in PERSONAS.items())
    raw = (await asyncio.to_thread(input, f"My personality [{choices}]: ")).strip()
    persona = persona_key(raw or get_settings().companion_persona)
    p = await eng.enroll(
        person_name=name, bot_nickname=nick, voiceprint=voiceprint, persona=persona
    )
    print(
        f"\nDone — you can call me {nick or '(unnamed)'} as your "
        f"{persona_title(persona)}. Say my name and I'll answer.  (id={p.id[:8]})"
    )


# ── the conversation loop ─────────────────────────────────────────
async def run_loop() -> None:
    from core.voice.stt_whisper import WhisperSTT

    eng = build_engine()
    if not await eng.profiles.all():
        print("No one's enrolled yet — run `mini enroll` first.")
        return

    stt = WhisperSTT(provider="local")
    voices = available_voices() or [(None, "text-only", "none")]
    vi = select_index(voices, get_settings().companion_voice)
    tts, label, _ = voices[vi]

    controls = _Controls()
    restore = _start_key_watcher(controls)
    print(f"(voice: {label})")
    print("Mini AI is listening.   O = observe · V = voice · Ctrl-C = quit\n")

    try:
        while True:
            if controls.cycle_voice.is_set():
                controls.cycle_voice.clear()
                vi = (vi + 1) % len(voices)
                tts, label, _ = voices[vi]
                print(f"\n🔊 voice: {label}", flush=True)

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

            mode = "observe" if controls.observe.is_set() else "talk"
            try:
                turn = await eng.handle_text(profile, text, mode=mode)
            except Exception as e:  # most likely Ollama not running / model not pulled
                model = get_settings().companion_model
                print(f"  (brain error — is Ollama up with '{model}'? {e})")
                continue
            await eng.profiles.blend_voiceprint(profile.id, voiceprint)

            tag = profile.person_name or profile.bot_nickname or profile.id[:8]
            if turn.spoke and turn.reply:
                print(f"  {tag}: {text}\n  {profile.bot_nickname or 'Mini'}: {turn.reply}")
                if tts is not None:
                    try:
                        await _speak(tts, turn.reply)
                    except Exception as e:  # a voice hiccup must not kill the chat
                        print(f"  (voice error, continuing text-only: {e})")
            else:
                print(f"  · ({turn.reason}) {tag}: {text}")
    finally:
        restore()
