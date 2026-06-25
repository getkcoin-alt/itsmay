"""Claude-powered terminal worker agent.

Each TerminalAgent runs its own agentic loop with bash access, launched as an
asyncio background task. Scrappy spawns them via the terminal connector and polls
for results; the Agents tab in the web console shows live status.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from core.brain.agent_loop import Done, Token, ToolResult, run_tool_loop
from core.brain.llm import LLMClient, Message
from core.connectors.base import InvocationContext
from core.logging import get_logger

log = get_logger(__name__)

Status = Literal["pending", "running", "done", "error"]

_SYSTEM = """\
You are a Terminal Agent — a skilled software engineer working for Scrappy Singh.
Your job: complete the assigned task using your bash tool.

Guidelines:
- Work step by step: reason briefly, run a command, read the output, continue.
- Keep commands focused and safe. Avoid destructive operations unless asked.
- If a command fails, diagnose the error and try a different approach.
- When the task is complete, write a concise summary of what you accomplished and stop calling tools.
- Never loop endlessly. If stuck after 3 attempts at something, explain the blocker and stop.
"""

_BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a shell command. Returns combined stdout+stderr (capped at 4 KB).",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Shell command to run",
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "Max seconds to wait (1–120)",
                },
            },
            "required": ["cmd"],
        },
    },
}


@dataclass
class LogEntry:
    kind: Literal["thought", "cmd", "output", "result", "error"]
    text: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class _BashRouter:
    """ToolRouter for a single terminal agent — exposes only the bash tool."""

    def __init__(self, agent: TerminalAgent) -> None:
        self._agent = agent

    def tools_payload(self) -> list[dict]:
        return [_BASH_TOOL]

    def is_client_tool(self, name: str) -> bool:
        return False

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        if name != "bash":
            return f"error: unknown tool {name!r}"
        cmd = args.get("cmd", "").strip()
        timeout = min(max(int(args.get("timeout", 30)), 1), 120)
        if not cmd:
            return "(empty command)"
        self._agent._add_log("cmd", cmd)
        output = await _exec(cmd, cwd=self._agent.work_dir, timeout=timeout)
        self._agent._add_log("output", output)
        return output


class TerminalAgent:
    def __init__(self, task: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.task = task
        self.status: Status = "pending"
        self.log: list[LogEntry] = []
        self.result: str | None = None
        self.created_at = datetime.now(UTC).isoformat()
        self.finished_at: str | None = None
        self.work_dir = f"/tmp/scrappy-agent-{self.id}"
        self._asyncio_task: asyncio.Task | None = None

    def launch(self, llm: LLMClient) -> None:
        """Start the agent loop as a background asyncio task."""
        self._asyncio_task = asyncio.create_task(
            self._run(llm), name=f"agent-{self.id}"
        )

    def cancel(self) -> bool:
        """Cancel a running agent. Returns True if it was running and got cancelled."""
        if self._asyncio_task and not self._asyncio_task.done():
            self._asyncio_task.cancel()
            self.status = "error"
            self.result = "Cancelled by user"
            self.finished_at = datetime.now(UTC).isoformat()
            self._add_log("error", "Cancelled by user")
            return True
        return False

    async def _run(self, llm: LLMClient) -> None:  # noqa: ARG002 — llm kept for API compat
        self.status = "running"
        os.makedirs(self.work_dir, exist_ok=True)

        # Each agent owns an isolated LLM client so its streaming calls never
        # share an httpx connection pool with the parent chat request. Without
        # this, concurrent streams on the shared client cause "stream has been
        # closed" errors mid-conversation.
        own_llm = LLMClient()

        messages: list[Message] = [
            Message(role="system", content=_SYSTEM),
            Message(role="user", content=self.task),
        ]
        router = _BashRouter(self)
        ctx = InvocationContext()
        thought_buf: list[str] = []

        try:
            async for ev in run_tool_loop(
                llm=own_llm,
                messages=messages,
                router=router,
                ctx=ctx,
                temperature=0.3,
                max_iters=20,
            ):
                if isinstance(ev, Token):
                    thought_buf.append(ev.text)
                elif isinstance(ev, ToolResult):
                    # Flush the model's thinking that preceded this tool call.
                    thought = "".join(thought_buf).strip()
                    thought_buf = []
                    if thought:
                        self._add_log("thought", thought)
                elif isinstance(ev, Done):
                    final = "".join(thought_buf).strip()
                    thought_buf = []
                    self.result = final or "(completed)"
                    if final:
                        self._add_log("result", final)

            self.status = "done"

        except Exception as exc:
            self.status = "error"
            self.result = str(exc)
            self._add_log("error", str(exc))
            log.exception("terminal_agent.failed", agent_id=self.id)

        finally:
            await own_llm.aclose()

        self.finished_at = datetime.now(UTC).isoformat()
        log.info("terminal_agent.done", agent_id=self.id, status=self.status)

    def _add_log(self, kind: str, text: str) -> None:
        self.log.append(LogEntry(kind=kind, text=text))  # type: ignore[arg-type]

    def to_dict(self, *, full: bool = False) -> dict:
        d: dict = {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "work_dir": self.work_dir,
            "log_count": len(self.log),
        }
        if full:
            d["log"] = [{"kind": e.kind, "text": e.text, "ts": e.ts} for e in self.log]
        return d


async def _exec(cmd: str, cwd: str, timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(timeout))
        text = out.decode(errors="replace")
        if len(text) > 4096:
            text = text[:4096] + "\n…[truncated]"
        return text or "(no output)"
    except asyncio.TimeoutError:
        try:
            proc.kill()  # type: ignore[union-attr]
        except Exception:
            pass
        return f"[timed out after {timeout}s]"
    except Exception as exc:
        return f"[error: {exc}]"
