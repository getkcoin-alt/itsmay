"""Offline local TTS (Piper) — the sovereign voice for Mini AI.

Mirrors the `ElevenLabsTTS.stream(text) -> AsyncIterator[bytes]` surface so the
two are interchangeable (selected by `TTS_PROVIDER`), but yields **raw int16 PCM
mono** at `self.sample_rate`, ideal for piping straight to the local audio device
with no network. The Piper voice is lazy-loaded (heavy, edge-only); inject `synth`
in tests to avoid the model entirely.

Childish/neutral voice = pick a youthful Piper voice model (`PIPER_VOICE_PATH`) and
tune `length_scale`. A true pitch-shift for an even younger timbre is a later
post-processing step.
"""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

_CHUNK = 4096


class LocalTTS:
    def __init__(
        self,
        voice_path: str | None = None,
        *,
        length_scale: float | None = None,
        sample_rate: int = 22050,
        synth: Callable[[str], Iterable[bytes]] | None = None,
    ) -> None:
        s = get_settings()
        self.voice_path = voice_path if voice_path is not None else s.piper_voice_path
        self.length_scale = (
            length_scale if length_scale is not None else s.piper_length_scale
        )
        self.sample_rate = sample_rate
        self._synth = synth  # injectable: text -> iterable[pcm bytes]
        self._voice: Any | None = None
        self._lock = threading.Lock()

    async def aclose(self) -> None:  # parity with ElevenLabsTTS
        return None

    @property
    def configured(self) -> bool:
        if self._synth is not None:
            return True
        return bool(self.voice_path and Path(self.voice_path).expanduser().exists())

    def _ensure(self) -> Any:
        with self._lock:
            if self._voice is None:
                try:
                    from piper import PiperVoice
                except ImportError as e:
                    raise RuntimeError(
                        "piper-tts not installed — install the companion extra: "
                        'pip install -e ".[mini]"'
                    ) from e
                if not self.voice_path:
                    raise RuntimeError(
                        "PIPER_VOICE_PATH not set — download a Piper .onnx voice "
                        "(see https://github.com/rhasspy/piper)"
                    )
                log.info("companion.piper.loading", voice=self.voice_path)
                self._voice = PiperVoice.load(str(Path(self.voice_path).expanduser()))
                self.sample_rate = self._voice.config.sample_rate
                log.info("companion.piper.loaded", sample_rate=self.sample_rate)
            return self._voice

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield int16 PCM mono chunks for `text`."""
        if self._synth is not None:
            for chunk in self._synth(text):
                if chunk:
                    yield chunk
            return
        pcm = await asyncio.to_thread(self._synth_sync, text)
        for i in range(0, len(pcm), _CHUNK):
            yield pcm[i : i + _CHUNK]

    def _synth_sync(self, text: str) -> bytes:
        """Run Piper and return raw PCM frames. Piper writes a WAV; we read its
        frames back out so callers get pure PCM at self.sample_rate."""
        voice = self._ensure()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            voice.synthesize(text, wf, length_scale=self.length_scale)
        buf.seek(0)
        with wave.open(buf, "rb") as wf:
            self.sample_rate = wf.getframerate()
            return wf.readframes(wf.getnframes())
