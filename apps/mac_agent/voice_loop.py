"""Push-to-talk voice loop for the Mac.

Workflow per turn:
    1. Press ENTER  → start recording mic
    2. Press ENTER  → stop, ship to /v1/voice/transcribe
    3. Transcript sent to /v1/chat (SSE)
    4. Streaming tokens are sentence-buffered → /v1/voice/speak per sentence
    5. MP3 chunks play sequentially via `afplay` so you hear Scrappy talk back
       while he is still generating the rest.

Session id is persisted to ~/.vault_zeta_session so memory carries across runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

# Load .env from cwd so VAULT_API_BASE / VAULT_API_KEY work without manual exports.
load_dotenv()

API_BASE = os.environ.get("VAULT_API_BASE", "http://127.0.0.1:8000")
API_KEY = os.environ.get("VAULT_API_KEY", "")
SAMPLE_RATE = 16000  # whisper native
MIN_RECORDING_SEC = 0.3
SESSION_FILE = Path.home() / ".vault_zeta_session"
SENTENCE_BREAK = re.compile(r"([.!?\n])\s+")


def _default_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


# ── mic capture ──────────────────────────────────────────────────
def record_until_enter() -> np.ndarray:
    """Block until user hits ENTER again, return mono int16 audio at SAMPLE_RATE."""
    chunks: list[np.ndarray] = []
    stop = threading.Event()

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            print(f"[mic warn] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    def wait_for_enter():
        try:
            input()
        except EOFError:
            pass
        stop.set()

    print("● recording…  (ENTER to stop)", flush=True)
    threading.Thread(target=wait_for_enter, daemon=True).start()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
        while not stop.is_set():
            sd.sleep(50)
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(chunks).flatten()


def write_wav(audio: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


# ── HTTP: server ↔ agent ─────────────────────────────────────────
async def transcribe(client: httpx.AsyncClient, wav_path: Path) -> str:
    """POST audio to /v1/voice/transcribe. 45s timeout — Groq Whisper is
    usually <3s; longer than that and we want to surface the hang."""
    with open(wav_path, "rb") as f:
        files = {"audio": ("mic.wav", f, "audio/wav")}
        r = await client.post(f"{API_BASE}/v1/voice/transcribe", files=files, timeout=45)
    r.raise_for_status()
    return r.json()["text"]


async def stream_chat(client: httpx.AsyncClient, text: str, session_id: str | None):
    """Yields ('token'|'session'|'done'|'error', payload). Caller consumes deltas."""
    body: dict = {"message": text, "channel": "voice_mac"}
    if session_id:
        body["session_id"] = session_id
    headers = {"Accept": "text/event-stream"}
    event = "message"
    async with client.stream(
        "POST", f"{API_BASE}/v1/chat", json=body, headers=headers, timeout=180
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line:
                continue
            if line.startswith(":"):  # SSE comment / keepalive
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if line.startswith("data:"):
                # SSE: strip exactly ONE leading space (the field-format space),
                # not all whitespace — token deltas often start with a space.
                data = line[6:] if line.startswith("data: ") else line[5:]
                yield event, data
                if event == "done" or event == "error":
                    return


async def tts_to_file(client: httpx.AsyncClient, text: str, out: Path) -> None:
    body = {"text": text}
    async with client.stream("POST", f"{API_BASE}/v1/voice/speak", json=body, timeout=60) as resp:
        resp.raise_for_status()
        with open(out, "wb") as f:
            async for chunk in resp.aiter_bytes():
                f.write(chunk)


# ── audio playback queue ────────────────────────────────────────
class AudioPlayer:
    """Background thread that plays queued MP3 files sequentially via `afplay`."""

    _SENTINEL: object = object()

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self.q.get()
            if item is self._SENTINEL:
                return
            path: Path = item
            try:
                subprocess.run(["afplay", str(path)], check=False)
            finally:
                path.unlink(missing_ok=True)

    def play(self, path: Path) -> None:
        self.q.put(path)

    async def drain(self) -> None:
        while not self.q.empty():
            await asyncio.sleep(0.05)

    def shutdown(self) -> None:
        self.q.put(self._SENTINEL)
        self._thread.join(timeout=2)


# ── sentence splitter ───────────────────────────────────────────
def pop_sentence(buf: str) -> tuple[str | None, str]:
    """If `buf` contains a full sentence, return (sentence, remainder). Else (None, buf)."""
    m = SENTENCE_BREAK.search(buf)
    if not m:
        return None, buf
    end = m.end()
    return buf[:end].strip(), buf[end:]


# ── main loop ───────────────────────────────────────────────────
async def turn(client: httpx.AsyncClient, player: AudioPlayer, session_id: str | None) -> str | None:
    """One push-to-talk round. Returns the (possibly new) session_id to persist."""
    input("\n[ENTER to talk]")
    audio = record_until_enter()
    if audio.size < SAMPLE_RATE * MIN_RECORDING_SEC:
        print("(too short, skipped)")
        return session_id

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        write_wav(audio, wav_path)
        print("⌁ transcribing…", flush=True)
        text = await transcribe(client, wav_path)
    except httpx.HTTPError as e:
        print(f"\n[transcribe failed] {type(e).__name__}: {e}", file=sys.stderr)
        return session_id
    finally:
        wav_path.unlink(missing_ok=True)

    if not text.strip():
        print("(silence)")
        return session_id

    print(f"\nyou: {text}")
    print("scrappy: ", end="", flush=True)

    full_response: list[str] = []
    buf = ""
    tts_tasks: list[asyncio.Task] = []
    new_session_id = session_id

    async def synth(snippet: str) -> None:
        out = Path(tempfile.mkstemp(suffix=".mp3")[1])
        try:
            await tts_to_file(client, snippet, out)
        except Exception as e:
            print(f"\n[tts error] {e}", file=sys.stderr)
            out.unlink(missing_ok=True)
            return
        player.play(out)

    async for evt, data in stream_chat(client, text, session_id):
        if evt == "session":
            new_session_id = json.loads(data)["session_id"]
        elif evt == "token":
            full_response.append(data)
            print(data, end="", flush=True)
            buf += data
            while True:
                sentence, buf = pop_sentence(buf)
                if sentence is None:
                    break
                tts_tasks.append(asyncio.create_task(synth(sentence)))
        elif evt == "done":
            break
        elif evt == "error":
            print(f"\n[chat error] {data}", file=sys.stderr)
            break

    if buf.strip():
        tts_tasks.append(asyncio.create_task(synth(buf.strip())))

    if tts_tasks:
        await asyncio.gather(*tts_tasks, return_exceptions=True)
    await player.drain()
    print()  # newline after streaming response
    return new_session_id


async def main() -> None:
    print(f"Vault Zeta voice agent  →  {API_BASE}")
    print("Loading session…", end=" ", flush=True)
    session_id: str | None = (
        SESSION_FILE.read_text().strip() if SESSION_FILE.exists() else None
    )
    print(session_id or "new session")

    headers = _default_headers()
    if API_KEY:
        print(f"  auth: bearer-token ({len(API_KEY)} chars)")
    else:
        print("  auth: none (local mode)")

    # Probe server availability up-front.
    try:
        async with httpx.AsyncClient(headers=headers) as probe:
            r = await probe.get(f"{API_BASE}/v1/health", timeout=5)
            r.raise_for_status()
    except Exception as e:
        print(f"✗ Server not reachable at {API_BASE} — start the API first.\n   {e}")
        return

    player = AudioPlayer()
    try:
        async with httpx.AsyncClient(headers=headers) as client:
            while True:
                session_id = await turn(client, player, session_id)
                if session_id:
                    SESSION_FILE.write_text(session_id)
    except (KeyboardInterrupt, EOFError):
        print("\nbye.")
    finally:
        player.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
