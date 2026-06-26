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
    future: asyncio.Future | None = field(default=None)

    def to_payload(self) -> dict:
        return {
            "command_id": self.id,
            "agent_id": self.agent_id,
            "kind": self.kind,
            "cmd": self.cmd,
            "timeout": self.timeout,
            "task": self.task,
            "thought": self.thought,
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
    ) -> str:
        """Queue a command for the worker and await its output string."""
        loop = asyncio.get_running_loop()
        command = WorkerCommand(
            id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            kind=kind,
            cmd=cmd,
            timeout=timeout,
            task=task,
            thought=thought,
            future=loop.create_future(),
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


_bridge: WorkerBridge | None = None


def get_worker_bridge() -> WorkerBridge:
    global _bridge
    if _bridge is None:
        _bridge = WorkerBridge()
    return _bridge
