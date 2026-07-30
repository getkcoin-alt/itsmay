"""In-memory bridge between server-side terminal agents and a local Mac worker.

A terminal agent *reasons* on the server (Railway) but its shell commands should
run on Karnveer's Mac — where brew, the browser, his files, and the `claude` CLI
actually live. This bridge is the hand-off point:

    agent  →  submit(cmd)  →  [queue]  →  next_command()  →  scrappy worker
    agent  ←   result      ←  complete(id, output)  ←  worker runs it locally

If no worker is connected (`worker_online()` is False) the agent falls back to
executing the command inside the server container, preserving prior behaviour.

The whole API process is one event loop (uvicorn), so a plain asyncio.Queue plus
per-command futures is all the coordination required. Multiple workers may poll;
results route back by command id, so work-stealing is safe.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class WorkerCommand:
    id: str
    agent_id: str
    kind: str  # "bash" | "claude"
    cmd: str
    timeout: int
    task: str = ""
    thought: str = ""
    # Where the worker should run this. "" = the per-agent scratch workspace;
    # "repo" = Scrappy's own itsmay checkout on the Mac (self-modification needs a
    # real git repo, not an empty scratch dir).
    workdir: str = ""
    # Ask the worker to run this (claude) command in streaming mode and POST live
    # milestones back — set for coder.build so a long build narrates itself.
    stream_progress: bool = False
    future: asyncio.Future | None = field(default=None)
    # Server-side live-progress sink: called with each milestone the worker
    # streams back for THIS command (via POST /v1/worker/progress) while it's
    # still running. Deliberately NOT part of to_payload — it never leaves the
    # server; the worker only knows the command_id to attach progress to.
    on_progress: Callable[[str], None] | None = field(default=None)

    def to_payload(self) -> dict:
        return {
            "command_id": self.id,
            "agent_id": self.agent_id,
            "kind": self.kind,
            "cmd": self.cmd,
            "timeout": self.timeout,
            "task": self.task,
            "thought": self.thought,
            "workdir": self.workdir,
            "stream_progress": self.stream_progress,
        }


class WorkerBridge:
    # A worker counts as online if it polled or pinged within this many seconds.
    ONLINE_WINDOW = 60.0

    def __init__(self) -> None:
        self._queue: asyncio.Queue[WorkerCommand] = asyncio.Queue()
        self._pending: dict[str, WorkerCommand] = {}
        self._last_seen: float = 0.0

    # ── presence ─────────────────────────────────────────────────
    def touch(self) -> None:
        """Mark the worker as alive (called by poll + heartbeat endpoints)."""
        self._last_seen = time.monotonic()

    def worker_online(self) -> bool:
        return (time.monotonic() - self._last_seen) < self.ONLINE_WINDOW

    def seconds_since_seen(self) -> float | None:
        if self._last_seen == 0.0:
            return None
        return time.monotonic() - self._last_seen

    # ── producer side (agents) ───────────────────────────────────
    async def submit(
        self,
        *,
        agent_id: str,
        kind: str,
        cmd: str,
        timeout: int,
        task: str = "",
        thought: str = "",
        workdir: str = "",
        stream_progress: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Queue a command for the worker and await its output string.

        `on_progress`, if given, is called with each live milestone the worker
        streams back for this command while it runs (see `progress`);
        `stream_progress` tells the worker to run in streaming mode so those
        milestones actually flow."""
        loop = asyncio.get_running_loop()
        command = WorkerCommand(
            id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            kind=kind,
            cmd=cmd,
            timeout=timeout,
            task=task,
            thought=thought,
            workdir=workdir,
            stream_progress=stream_progress,
            future=loop.create_future(),
            on_progress=on_progress,
        )
        self._pending[command.id] = command
        await self._queue.put(command)
        try:
            # The worker gets the command's own budget plus transit slack.
            return await asyncio.wait_for(command.future, timeout=timeout + 25)
        except TimeoutError:
            return (
                f"[worker did not return within {timeout}s — is `scrappy worker` "
                "still connected?]"
            )
        finally:
            self._pending.pop(command.id, None)

    # ── consumer side (worker) ───────────────────────────────────
    async def next_command(self, wait: float = 25.0) -> WorkerCommand | None:
        """Long-poll: return the next queued command, or None after `wait` s."""
        self.touch()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=wait)
        except TimeoutError:
            return None

    def complete(self, command_id: str, output: str) -> bool:
        """Resolve a command the worker finished. False if unknown/already done."""
        command = self._pending.get(command_id)
        if command is None or command.future is None or command.future.done():
            return False
        command.future.set_result(output)
        return True

    def progress(self, command_id: str, chunk: str) -> bool:
        """Route a live progress milestone from the worker to the command's sink.

        Best-effort narration only — never affects the command's result. Returns
        False when the command is unknown, already finished, or has no sink (most
        commands don't); a raising sink is swallowed so a bad narrator can't break
        the in-flight command."""
        command = self._pending.get(command_id)
        if command is None or command.on_progress is None:
            return False
        if command.future is not None and command.future.done():
            return False
        try:
            command.on_progress(chunk)
        except Exception as e:
            log.warning("worker.progress_sink_failed", command_id=command_id, err=str(e))
            return False
        return True


_bridge: WorkerBridge | None = None


def get_worker_bridge() -> WorkerBridge:
    global _bridge
    if _bridge is None:
        _bridge = WorkerBridge()
    return _bridge
