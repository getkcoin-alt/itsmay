"""Voice Activity Detection helpers for the Mac voice agent.

Uses webrtcvad (Google's WebRTC VAD). If not installed the classes raise
ImportError on construction so the caller can fall back to push-to-talk.
"""
from __future__ import annotations

import collections
import threading

import numpy as np
import sounddevice as sd

try:
    import webrtcvad as _webrtcvad
    _HAS_WEBRTCVAD = True
except ImportError:
    _HAS_WEBRTCVAD = False

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = 480  # 30ms × 16000Hz
FRAME_BYTES = 960    # 480 samples × 2 bytes (int16)


class VADRecorder:
    """Auto-detect speech onset and end, return accumulated int16 ndarray.

    Waits silently until min_speech_ms of speech is heard, records until
    silence_ms of silence, then returns. A pre-roll buffer captures audio
    that arrived before the speech trigger fired.
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        min_speech_ms: int = 200,
        silence_ms: int = 700,
        max_duration_s: float = 30.0,
        pre_roll_ms: int = 300,
    ) -> None:
        if not _HAS_WEBRTCVAD:
            raise ImportError("webrtcvad is not installed — pip install webrtcvad")
        self._vad = _webrtcvad.Vad(aggressiveness)
        self._min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self._silence_frames = max(1, silence_ms // FRAME_MS)
        self._max_frames = int(max_duration_s * 1000 / FRAME_MS)
        self._pre_roll_n = max(1, pre_roll_ms // FRAME_MS)

    def record(self, stop_event: threading.Event | None = None) -> np.ndarray | None:
        """Block until speech detected, then until trailing silence. Returns int16 audio."""
        ring: collections.deque[bytes] = collections.deque(maxlen=self._pre_roll_n)
        recorded: list[bytes] = []
        speech_count = 0
        silence_count = 0
        in_speech = False

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
        ) as stream:
            while True:
                if stop_event and stop_event.is_set():
                    return None
                raw, _ = stream.read(FRAME_SAMPLES)
                frame = bytes(raw)[:FRAME_BYTES]
                if len(frame) < FRAME_BYTES:
                    continue
                try:
                    is_speech = self._vad.is_speech(frame, SAMPLE_RATE)
                except Exception:
                    is_speech = False

                if not in_speech:
                    ring.append(frame)
                    if is_speech:
                        speech_count += 1
                        if speech_count >= self._min_speech_frames:
                            in_speech = True
                            recorded = list(ring)
                            silence_count = 0
                    else:
                        speech_count = max(0, speech_count - 1)
                else:
                    recorded.append(frame)
                    if is_speech:
                        silence_count = 0
                    else:
                        silence_count += 1
                        if silence_count >= self._silence_frames:
                            break
                    if len(recorded) >= self._max_frames:
                        break

        if not recorded:
            return None
        return np.frombuffer(b"".join(recorded), dtype=np.int16)


class BargeInMonitor:
    """Background thread that fires `detected` when sustained speech is heard.

    Start before TTS plays. If `detected` is set, the caller should interrupt
    the AudioPlayer and start a new recording turn immediately.
    """

    def __init__(self, aggressiveness: int = 2, trigger_ms: int = 160) -> None:
        if not _HAS_WEBRTCVAD:
            raise ImportError("webrtcvad is not installed")
        self._vad = _webrtcvad.Vad(aggressiveness)
        self._trigger_frames = max(1, trigger_ms // FRAME_MS)
        self.detected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.detected.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-in")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def _run(self) -> None:
        speech_count = 0
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
            ) as stream:
                while not self._stop.is_set():
                    raw, _ = stream.read(FRAME_SAMPLES)
                    frame = bytes(raw)[:FRAME_BYTES]
                    if len(frame) < FRAME_BYTES:
                        continue
                    try:
                        is_speech = self._vad.is_speech(frame, SAMPLE_RATE)
                    except Exception:
                        is_speech = False
                    if is_speech:
                        speech_count += 1
                        if speech_count >= self._trigger_frames:
                            self.detected.set()
                            break
                    else:
                        speech_count = max(0, speech_count - 1)
        except Exception:
            pass
