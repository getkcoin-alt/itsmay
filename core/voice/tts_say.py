"""macOS `say` TTS — a zero-setup local voice for Mini AI.

No model download, no Piper voice file: it shells out to the built-in `say`
command, which speaks directly to the device's audio. Used as the companion's
voice when Piper isn't configured (Piper stays the better, cross-platform option).

Unlike the streaming PCM engines, this plays the audio itself, so callers invoke
`await speak(text)` rather than streaming bytes. The subprocess runner is
injectable for tests (so CI never needs a real `say`).
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


class MacSayTTS:
    def __init__(
        self,
        voice: str | None = None,
        rate: int | None = None,
        *,
        exec_: Callable[[list[str]], Awaitable[None]] | None = None,
    ) -> None:
        s = get_settings()
        self.voice = voice if voice is not None else s.say_voice
        self.rate = rate if rate is not None else s.say_rate
        self._exec = exec_  # injectable: async (argv) -> None

    @property
    def configured(self) -> bool:
        return self._exec is not None or shutil.which("say") is not None

    def _args(self, text: str) -> list[str]:
        argv = ["say"]
        if self.voice:
            argv += ["-v", self.voice]
        if self.rate:
            argv += ["-r", str(self.rate)]
        argv.append(text)
        return argv

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        argv = self._args(text)
        if self._exec is not None:
            await self._exec(argv)
            return
        proc = await asyncio.create_subprocess_exec(*argv)
        await proc.wait()

    async def aclose(self) -> None:  # parity with the other TTS engines
        return None
