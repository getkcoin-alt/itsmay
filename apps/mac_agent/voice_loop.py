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
import shlex
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

from apps.mac_agent.chrome import format_tab_list, list_tabs, read_tab_text
from apps.mac_agent.tab_watcher import TabWatcher

try:
    from apps.mac_agent.vad import BargeInMonitor, EnergyVADRecorder, VADRecorder
    _HAS_VAD = True
except ImportError:
    _HAS_VAD = False
    EnergyVADRecorder = None  # type: ignore[assignment,misc]

# Load .env from cwd so VAULT_API_BASE / VAULT_API_KEY work without manual exports.
load_dotenv()

API_BASE = os.environ.get("VAULT_API_BASE", "http://127.0.0.1:8000")
API_KEY = os.environ.get("VAULT_API_KEY", "")
SAMPLE_RATE = 16000  # whisper native
MIN_RECORDING_SEC = 0.3
SESSION_FILE = Path.home() / ".vault_zeta_session"
# Phrase break — fires sooner than full sentence so first audio starts ~250ms earlier.
# We also enforce MIN_CHUNK_CHARS so we don't TTS single words.
PHRASE_BREAK = re.compile(r"([.!?\n,;—–:])\s+")
MIN_CHUNK_CHARS = 18

# Mac tools that require explicit confirmation before running.
APPROVAL_REQUIRED = {"mac.run_applescript"}

# Module-level watcher — only one tab watched at a time.
_active_watcher: TabWatcher | None = None

# The one live Claude Code session Scrappy is conducting (Terminal window id).
# Follow-up prompts are typed into this window instead of opening a new one.
_claude_session: dict[str, str | None] = {"window_id": None}

VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", "2"))
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "700"))

_TOOL_ACK: dict[str, str] = {
    "web": "Searching.",
    "web.search": "Searching.",
    "web.fetch": "Fetching that.",
    "memory": "Checking my memory.",
    "memory.search": "Checking my memory.",
    "memory.save": "Got it.",
    "terminal": "Running that now.",
    "terminal.spawn": "Spinning that up.",
    "coder": "Getting Claude on it.",
    "coder.code": "Getting Claude on it.",
    "ask_researcher": "Looking into it.",
    "ask_engineer": "On it.",
    "ask_analyst": "Analyzing.",
    "gmail": "Checking your email.",
    "cal": "Checking your calendar.",
}


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
        "POST", f"{API_BASE}/v1/chat", json=body, headers=headers, timeout=600
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
    """Background thread that plays queued MP3 files sequentially via `afplay`.

    Uses Popen (not run) so `interrupt()` can kill mid-sentence for barge-in.
    """

    _SENTINEL: object = object()

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self._busy = threading.Event()
        self._current_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self.q.get()
            if item is self._SENTINEL:
                return
            path: Path = item
            self._busy.set()
            try:
                proc = subprocess.Popen(
                    ["afplay", str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with self._lock:
                    self._current_proc = proc
                proc.wait()
                with self._lock:
                    self._current_proc = None
            finally:
                path.unlink(missing_ok=True)
                self._busy.clear()

    def play(self, path: Path) -> None:
        self.q.put(path)

    def interrupt(self) -> None:
        """Kill current audio and discard all queued items (for barge-in)."""
        while True:
            try:
                item = self.q.get_nowait()
                if isinstance(item, Path):
                    item.unlink(missing_ok=True)
            except queue.Empty:
                break
        with self._lock:
            proc = self._current_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._busy.clear()

    async def drain(self) -> None:
        while not self.q.empty() or self._busy.is_set():
            await asyncio.sleep(0.05)

    def shutdown(self) -> None:
        self.q.put(self._SENTINEL)
        self._thread.join(timeout=2)


# ── Mac tool execution (client-side) ────────────────────────────
def _confirm(prompt: str) -> bool:
    print(f"\n[approval needed] {prompt}", flush=True)
    try:
        ans = input("    type 'y' then ENTER to allow, anything else to skip: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans == "y"


def _run_silent(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a subprocess with stdin pinned to /dev/null so the child can't
    swallow the user's ENTER keystroke while it's running."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(cmd, check=False, **kw)


def _terminal_window_exists(window_id: str | None) -> bool:
    """True if a Terminal window with this id is still open."""
    if not window_id or not str(window_id).isdigit():
        return False
    script = f"tell application \"Terminal\" to return (exists window id {window_id})"
    try:
        r = _run_silent(["osascript", "-e", script], capture_output=True, text=True)
    except Exception:
        return False
    return (r.stdout or "").strip().lower() == "true"


def execute_mac_tool(name: str, args: dict) -> str:
    """Run a `mac.*` or `browser.*` tool locally and return a short status.

    All commands use explicit arg lists — never shell=True. stdin is pinned
    to DEVNULL on every child so they can't eat keystrokes meant for the
    record-until-enter loop. AppleScript strings are escaped via json.dumps.
    """
    global _active_watcher
    try:
        if name == "mac.open_app":
            app = str(args.get("name", "")).strip()
            if not app:
                return "[mac.open_app] missing 'name'"
            _run_silent(["open", "-a", app])
            return f"opened app: {app}"

        if name == "mac.claude_code":
            prompt = str(args.get("prompt", "")).strip()
            if not prompt:
                return "[mac.claude_code] missing prompt"

            # Continue the live session: type the follow-up into the existing
            # Claude Code window instead of opening a second one.
            winid = _claude_session.get("window_id")
            if winid and _terminal_window_exists(winid):
                send = (
                    f"tell application \"Terminal\" to do script "
                    f"{json.dumps(prompt)} in window id {winid}"
                )
                r = _run_silent(["osascript", "-e", send], capture_output=True, text=True)
                err = (r.stderr or "").strip()
                if err:
                    return f"[mac.claude_code] {err}"
                return f"sent to the open Claude Code session (window {winid}): {prompt[:60]}"

            # No live session — start one and remember its window.
            workdir = os.path.expanduser(
                str(args.get("dir") or os.environ.get("SCRAPPY_CODE_DIR", "~/scrappy-workspace"))
            )
            flags = os.environ.get("SCRAPPY_CLAUDE_FLAGS", "")
            inner = (
                f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)} && "
                f"claude {flags} {shlex.quote(prompt)}"
            ).strip()
            script = (
                f'tell application "Terminal"\nactivate\n'
                f'do script {json.dumps(inner)}\n'
                f'return id of front window as string\nend tell'
            )
            r = _run_silent(["osascript", "-e", script], capture_output=True, text=True)
            err = (r.stderr or "").strip()
            if err:
                return f"[mac.claude_code] {err}"
            _claude_session["window_id"] = (r.stdout or "").strip() or None
            return (
                f"started a Claude Code session (window {_claude_session['window_id']}, "
                f"dir {workdir}) for: {prompt[:60]}"
            )

        if name == "mac.open_url":
            url = str(args.get("url", "")).strip()
            if not url:
                return "[mac.open_url] missing 'url'"
            _run_silent(["open", url])
            return f"opened url: {url}"

        if name == "mac.notify":
            title = str(args.get("title", "Scrappy"))
            body = str(args.get("body", ""))
            script = (
                f"display notification {json.dumps(body)} "
                f"with title {json.dumps(title)}"
            )
            _run_silent(["osascript", "-e", script])
            return f"notified: {title}"

        if name == "mac.say":
            text = str(args.get("text", ""))
            if not text:
                return "[mac.say] empty text"
            _run_silent(["say", text])
            return "spoke (via macOS say)"

        if name == "mac.run_applescript":
            script = str(args.get("script", "")).strip()
            if not script:
                return "[mac.run_applescript] empty script"
            if name in APPROVAL_REQUIRED:
                print(f"\n--- script ---\n{script}\n--- end ---")
                if not _confirm("Scrappy wants to run the AppleScript above."):
                    return "[mac.run_applescript] declined by user"
            r = _run_silent(
                ["osascript", "-e", script], capture_output=True, text=True
            )
            out = (r.stdout or "").strip() or "(no output)"
            err = (r.stderr or "").strip()
            return f"ran applescript → {out}" + (f"  | stderr: {err}" if err else "")

        # ── browser tools ─────────────────────────────────────────
        if name == "browser.list_tabs":
            tabs = list_tabs()
            return format_tab_list(tabs)

        if name == "browser.read_tab":
            win = int(args.get("win", 1))
            tab = int(args.get("tab", 1))
            content = read_tab_text(win, tab)
            return content[:8000] if content else "(empty tab)"

        if name == "browser.watch_start":
            if _active_watcher and _active_watcher.running:
                _active_watcher.stop()
            win = int(args.get("win", 1))
            tab = int(args.get("tab", 1))
            interval = int(args.get("interval", 120))
            # Resolve title/url for the chosen tab.
            tabs = list_tabs()
            meta = next((t for t in tabs if t["win"] == win and t["tab"] == tab), {})
            _active_watcher = TabWatcher(
                win=win,
                tab=tab,
                interval=interval,
                api_base=API_BASE,
                api_key=API_KEY,
                title=meta.get("title", ""),
                url=meta.get("url", ""),
            )
            return _active_watcher.start()

        if name == "browser.watch_stop":
            if _active_watcher and _active_watcher.running:
                return _active_watcher.stop()
            return "no active tab watch"

        return f"[mac] unknown tool: {name}"
    except Exception as e:  # never let a tool blow up the loop
        return f"[mac.error] {type(e).__name__}: {e}"


# ── live agent watcher ──────────────────────────────────────────
_watched_agents: set[str] = set()
_AGENT_ID_RE = re.compile(r'"agent_id"\s*:\s*"([0-9a-fA-F]{6,16})"')


def _open_agent_watcher(agent_id: str) -> None:
    """Open a Terminal window on the Mac that streams `scrappy watch <agent_id>`
    so Karnveer can watch the spawned agent think and run commands live."""
    if not agent_id or agent_id in _watched_agents:
        return
    _watched_agents.add(agent_id)
    if os.environ.get("SCRAPPY_NO_WATCH_WINDOW"):
        return
    scrappy_bin = Path(sys.executable).parent / "scrappy"
    cwd = os.getcwd()
    inner = (
        f"cd {shlex.quote(cwd)} && "
        f"{shlex.quote(str(scrappy_bin))} watch {shlex.quote(agent_id)}"
    )
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(inner)}\nend tell'
    try:
        _run_silent(["osascript", "-e", script])
        print(f"  🪟  opened a Terminal watching agent {agent_id}", flush=True)
    except Exception as e:
        print(f"  [watch window failed] {e}", file=sys.stderr)


def _maybe_open_watcher_from_status(payload: dict) -> None:
    """If a status event reports a terminal.spawn, pop open a live watch window."""
    if payload.get("tool") != "terminal.spawn":
        return
    m = _AGENT_ID_RE.search(str(payload.get("result", "")))
    if m:
        _open_agent_watcher(m.group(1))


# ── phrase splitter ─────────────────────────────────────────────
def pop_phrase(buf: str) -> tuple[str | None, str]:
    """Pop the first phrase ending at a natural break (. ! ? , ; : — \\n).

    Skips breaks that fire too early so we don't synthesize 1-2 word fragments;
    when the chunk is long enough (>= MIN_CHUNK_CHARS) it ships to TTS so the
    first audio bytes start arriving while the LLM is still emitting tokens.
    """
    last_end = 0
    for m in PHRASE_BREAK.finditer(buf):
        end = m.end()
        if end >= MIN_CHUNK_CHARS:
            return buf[:end].strip(), buf[end:]
        last_end = end
    return None, buf


# ── main loop ───────────────────────────────────────────────────
async def _record_audio(mute: threading.Event | None = None) -> np.ndarray | None:
    """Hands-free capture of one utterance.

    Prefers webrtcvad (most accurate); falls back to a pure-Python energy VAD
    (no native deps) so it stays hands-free even without webrtcvad; only drops to
    push-to-talk if audio capture itself is unavailable.
    """
    if _HAS_VAD:
        # webrtcvad path — raises ImportError if the wheel isn't installed.
        try:
            recorder = VADRecorder(aggressiveness=VAD_AGGRESSIVENESS, silence_ms=VAD_SILENCE_MS)
            print("\n● listening…", flush=True)
            return await asyncio.to_thread(recorder.record)
        except ImportError:
            pass  # no webrtcvad → energy VAD below
        except Exception as e:
            print(f"[vad] {e}", file=sys.stderr)

    if EnergyVADRecorder is not None:
        try:
            print("\n● listening…", flush=True)
            recorder = EnergyVADRecorder(silence_ms=VAD_SILENCE_MS)
            return await asyncio.to_thread(recorder.record, mute)
        except Exception as e:
            print(f"[energy-vad] {e} — push-to-talk", file=sys.stderr)

    input("\n[ENTER to talk]")
    return record_until_enter()


async def turn(
    client: httpx.AsyncClient,
    player: AudioPlayer,
    session_id: str | None,
    mute: threading.Event | None = None,
) -> str | None:
    """One VAD-powered round (or push-to-talk fallback). Returns updated session_id."""
    audio = await _record_audio(mute)
    if audio is None or audio.size < SAMPLE_RATE * MIN_RECORDING_SEC:
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
    barge_in: BargeInMonitor | None = None

    async def synth(snippet: str) -> None:
        out = Path(tempfile.mkstemp(suffix=".mp3")[1])
        try:
            await tts_to_file(client, snippet, out)
        except Exception as e:
            print(f"\n[tts error] {e}", file=sys.stderr)
            out.unlink(missing_ok=True)
            return
        player.play(out)

    if _HAS_VAD:
        try:
            barge_in = BargeInMonitor(aggressiveness=VAD_AGGRESSIVENESS)
            barge_in.start()
        except Exception:
            barge_in = None

    try:
        async for evt, data in stream_chat(client, text, session_id):
            if barge_in and barge_in.detected.is_set():
                print("\n⚡ [barge-in]", flush=True)
                player.interrupt()
                break

            if evt == "session":
                new_session_id = json.loads(data)["session_id"]
            elif evt == "token":
                full_response.append(data)
                print(data, end="", flush=True)
                buf += data
                while True:
                    phrase, buf = pop_phrase(buf)
                    if phrase is None:
                        break
                    tts_tasks.append(asyncio.create_task(synth(phrase)))
            elif evt == "tool_start":
                try:
                    payload = json.loads(data)
                    tool = payload.get("tool", "")
                    ack = _TOOL_ACK.get(tool) or _TOOL_ACK.get(tool.split(".")[0], "")
                    if ack:
                        if buf.strip():
                            tts_tasks.append(asyncio.create_task(synth(buf.strip())))
                            buf = ""
                        tts_tasks.append(asyncio.create_task(synth(ack)))
                    print(f"\n  → {tool}…", flush=True)
                except json.JSONDecodeError:
                    pass
            elif evt == "status":
                try:
                    payload = json.loads(data)
                    print(f"\n  ✓ {payload.get('tool', '?')}", flush=True)
                    # When Scrappy spawns an agent, pop open a Terminal window on
                    # the Mac streaming its live work, so it's visible — not buried
                    # on the server.
                    await asyncio.to_thread(_maybe_open_watcher_from_status, payload)
                except json.JSONDecodeError:
                    pass
            elif evt == "tool_call":
                try:
                    tc = json.loads(data)
                except json.JSONDecodeError:
                    print(f"\n[tool_call bad json] {data[:200]}", file=sys.stderr)
                    continue
                tname = tc.get("name", "")
                targs = tc.get("arguments") or {}
                print(f"\n→ {tname}({json.dumps(targs)})", flush=True)
                result = await asyncio.to_thread(execute_mac_tool, tname, targs)
                print(f"  {result}", flush=True)
            elif evt == "done":
                break
            elif evt == "error":
                # Speak a calm message instead of dying on a raw error blob.
                speak = "Sorry, something glitched on my end. Try that again."
                try:
                    payload = json.loads(data)
                    speak = payload.get("speak") or speak
                    print(
                        f"\n[{payload.get('kind', 'error')}] {payload.get('error', '')[:200]}",
                        file=sys.stderr,
                    )
                except json.JSONDecodeError:
                    print(f"\n[chat error] {data}", file=sys.stderr)
                tts_tasks.append(asyncio.create_task(synth(speak)))
                break
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:300] if e.response is not None else ""
        print(f"\n[chat http {e.response.status_code}] {body}", file=sys.stderr)
        return new_session_id
    except httpx.HTTPError as e:
        print(f"\n[chat network error] {type(e).__name__}: {e}", file=sys.stderr)
        return new_session_id
    finally:
        if barge_in:
            barge_in.stop()

    interrupted = barge_in is not None and barge_in.detected.is_set()
    if buf.strip() and not interrupted:
        tts_tasks.append(asyncio.create_task(synth(buf.strip())))

    if tts_tasks:
        await asyncio.gather(*tts_tasks, return_exceptions=True)
    if not interrupted:
        await player.drain()
    print()
    return new_session_id


def _mute_watcher(muted: threading.Event) -> None:
    """Background thread: press ENTER to toggle mute (pause listening)."""
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            return
        if line == "":  # stdin closed
            return
        if muted.is_set():
            muted.clear()
            print("🎙️  unmuted — listening", flush=True)
        else:
            muted.set()
            print("🔇 muted — press ENTER to resume", flush=True)


async def worker_status(client: httpx.AsyncClient) -> bool | None:
    """True if a Mac worker is connected, False if not, None if unknown."""
    try:
        r = await client.get(f"{API_BASE}/v1/worker/status", timeout=5)
        r.raise_for_status()
        return bool(r.json().get("online"))
    except Exception:
        return None


def _print_worker_status(online: bool | None) -> None:
    if online is True:
        print("  🖥️  Mac worker: connected — agents run on your Mac")
    elif online is False:
        print(
            "  🖥️  Mac worker: not running — agents run on the server. "
            "Start it with `scrappy worker`"
        )
    # None (unknown / older server) → stay quiet


async def main() -> None:
    mode = "VAD auto-detect" if _HAS_VAD else "push-to-talk"
    print(f"Vault Zeta voice agent  →  {API_BASE}  ({mode})")
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

    hands_free = _HAS_VAD or EnergyVADRecorder is not None
    muted: threading.Event | None = None
    if hands_free:
        muted = threading.Event()
        threading.Thread(target=_mute_watcher, args=(muted,), daemon=True).start()
        print("  hands-free — just talk. Press ENTER to mute/unmute, Ctrl+C to quit.")

    player = AudioPlayer()
    try:
        async with httpx.AsyncClient(headers=headers) as client:
            last_worker = await worker_status(client)
            _print_worker_status(last_worker)
            while True:
                if muted is not None and muted.is_set():
                    await asyncio.sleep(0.2)
                    continue
                session_id = await turn(client, player, session_id, muted)
                if session_id:
                    SESSION_FILE.write_text(session_id)
                # Surface worker connect/disconnect transitions between turns.
                now = await worker_status(client)
                if now is not None and now != last_worker:
                    if now:
                        print("\n  🖥️  Mac worker connected — agents now run on your Mac.")
                    else:
                        print(
                            "\n  🖥️  Mac worker disconnected — agents will run on the "
                            "server. Restart `scrappy worker` to bring it back."
                        )
                    last_worker = now
    except (KeyboardInterrupt, EOFError):
        print("\nbye.")
    finally:
        player.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
