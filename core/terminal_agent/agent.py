"""Claude-powered terminal worker agent.

Each TerminalAgent runs its own agentic loop, launched as an asyncio background
task. It *reasons* on the server (Railway), but its shell commands run on
Karnveer's Mac whenever a `scrappy worker` is connected (via core.worker.bridge)
— so brew, the browser, his files, and the `claude` CLI are all reachable. With
no worker connected it falls back to running commands in the server container.

Agents are memory-aware: at spawn they pull relevant facts from semantic memory,
and they can `memory.search` / `memory.save` while they work.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from core.brain.agent_loop import Done, Token, ToolResult, ToolStart, run_tool_loop
from core.brain.llm import LLMClient, Message
from core.config import get_settings
from core.connectors.base import InvocationContext
from core.connectors.memory.connector import MemoryConnector
from core.logging import get_logger
from core.worker.bridge import get_worker_bridge

log = get_logger(__name__)

Status = Literal["pending", "running", "done", "error"]

_SYSTEM = """\
You are a Terminal Agent — a skilled software engineer working for Scrappy Singh,
Karnveer's AI operator. You complete the assigned task on Karnveer's Mac.

Tools:
- bash: run a shell command on the Mac (a login shell — brew, git, python, node,
  and his files are all reachable). Returns combined stdout+stderr.
- claude: hand a heavy, self-contained coding/engineering task to Claude Code
  (the `claude` CLI). Use it for multi-file changes, debugging across a repo, or
  writing substantial code — give it ONE complete, standalone instruction and it
  returns its final result. Prefer bash for quick checks; reach for claude when
  the work is large.
- memory.search / memory.save: recall durable facts about Karnveer (his stack,
  preferences, projects) and persist new ones worth remembering long-term.

Guidelines:
- Work step by step: reason briefly, run a command, read the output, continue.
- Keep commands focused and safe. Avoid destructive operations unless asked.
- Each bash command runs in a fresh shell, so chain with && or use absolute paths
  rather than relying on a prior `cd` persisting.
- If a command fails, diagnose the error and try a different approach.
- When the task is complete, write a concise summary of what you accomplished and
  stop calling tools.
- Never loop endlessly. If stuck after 3 attempts at something, explain the
  blocker and stop.
- Be honest about blockers — never fake success. If the task can't actually be
  completed from a shell (it needs a human web signup/login, a GUI, a payment, or
  credentials/accounts you don't have — e.g. "create a Shopify store"), STOP early
  and say so plainly: state exactly what is blocking it and what a human must do.
  A truthful "I couldn't complete this because X; here's what's needed" is the
  correct result — never a fake "done".
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

_CLAUDE_TOOL = {
    "type": "function",
    "function": {
        "name": "claude",
        "description": (
            "Delegate a heavy coding/engineering task to Claude Code (the `claude` CLI) "
            "running on the Mac. Best for multi-file changes, repo-wide debugging, or "
            "writing substantial code. Pass ONE complete, self-contained instruction; "
            "returns Claude's final output. Requires a connected `scrappy worker`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "A complete, standalone instruction for Claude Code.",
                },
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": "Max seconds to wait (30–600).",
                },
            },
            "required": ["prompt"],
        },
    },
}


@dataclass
class LogEntry:
    kind: Literal["thought", "cmd", "output", "result", "error"]
    text: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class _AgentToolRouter:
    """Tool surface for one terminal agent: bash + claude (+ memory when wired)."""

    def __init__(self, agent: TerminalAgent) -> None:
        self._agent = agent
        self._memory = MemoryConnector() if agent.semantic is not None else None

    def tools_payload(self) -> list[dict]:
        tools = [_BASH_TOOL, _CLAUDE_TOOL]
        if self._memory is not None:
            tools.extend(
                {
                    "type": "function",
                    "function": {
                        "name": f"memory.{t.name}",
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in self._memory.manifest.tools
            )
        return tools

    def is_client_tool(self, name: str) -> bool:
        return False

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        agent = self._agent

        if name == "bash":
            cmd = str(args.get("cmd", "")).strip()
            if not cmd:
                return "(empty command)"
            timeout = min(max(int(args.get("timeout", 30) or 30), 1), 120)
            agent._add_log("cmd", cmd)
            output = await agent._run_command("bash", cmd, timeout)
            agent._add_log("output", output)
            return output

        if name == "claude":
            prompt = str(args.get("prompt", "")).strip()
            if not prompt:
                return "(empty prompt)"
            timeout = min(max(int(args.get("timeout", 300) or 300), 30), 600)
            agent._add_log("cmd", f"claude -p {prompt[:200]}")
            output = await agent._run_command("claude", prompt, timeout)
            agent._add_log("output", output)
            return output

        if name.startswith("memory.") and self._memory is not None:
            from core.connectors.registry import flatten_result

            action = name.split(".", 1)[1]
            res = await self._memory.invoke(action, args, ctx)
            return flatten_result(res)

        return f"error: unknown tool {name!r}"


class TerminalAgent:
    def __init__(
        self,
        task: str,
        *,
        embedder: Any | None = None,
        semantic: Any | None = None,
        user_uuid: Any | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.task = task
        self.status: Status = "pending"
        self.log: list[LogEntry] = []
        self.result: str | None = None
        self.created_at = datetime.now(UTC).isoformat()
        self.finished_at: str | None = None
        self.work_dir = f"/tmp/scrappy-agent-{self.id}"
        # Memory handles (RAG) — populated by the spawn path when available.
        self.embedder = embedder
        self.semantic = semantic
        self.user_uuid = user_uuid
        # The model's most recent reasoning, surfaced to the worker terminal so
        # Karnveer can watch *why* each command runs. Updated on every ToolStart.
        self._current_thought = ""
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

    # ── command execution: Mac worker if connected, else server ──
    async def _run_command(self, kind: str, cmd: str, timeout: int) -> str:
        """Run a bash/claude command on the connected Mac worker, or locally."""
        bridge = get_worker_bridge()
        if bridge.worker_online():
            return await bridge.submit(
                agent_id=self.id,
                kind=kind,
                cmd=cmd,
                timeout=timeout,
                task=self.task,
                thought=self._current_thought,
            )
        # No worker connected — fall back to the server container.
        if kind == "claude":
            return (
                "[claude requires the Mac worker — start it on your Mac with "
                "`scrappy worker`, then retry]"
            )
        return await _exec(cmd, cwd=self.work_dir, timeout=timeout)

    async def _run(self, llm: LLMClient) -> None:  # noqa: ARG002 — llm kept for API compat
        self.status = "running"
        os.makedirs(self.work_dir, exist_ok=True)

        # Each agent owns an isolated LLM client on the cheaper agent model, so
        # its many iterations don't drain the main model's daily token budget
        # (and its streaming never shares an httpx pool with the parent chat).
        own_llm = LLMClient(model=get_settings().llm_agent_model)

        ctx = InvocationContext(
            user_uuid=self.user_uuid,
            llm=own_llm,
            embedder=self.embedder,
            semantic=self.semantic,
        )

        system = _SYSTEM + await self._memory_preamble()
        messages: list[Message] = [
            Message(role="system", content=system),
            Message(role="user", content=self.task),
        ]
        router = _AgentToolRouter(self)
        thought_buf: list[str] = []

        try:
            async for ev in run_tool_loop(
                llm=own_llm,
                messages=messages,
                router=router,
                ctx=ctx,
                temperature=0.3,
                max_iters=10,
            ):
                if isinstance(ev, Token):
                    thought_buf.append(ev.text)
                elif isinstance(ev, ToolStart):
                    # Snapshot the reasoning that led to this tool so the worker
                    # can show it before running the command.
                    self._current_thought = "".join(thought_buf).strip()
                elif isinstance(ev, ToolResult):
                    thought = "".join(thought_buf).strip()
                    thought_buf = []
                    if thought:
                        self._add_log("thought", thought)
                elif isinstance(ev, Done):
                    final = "".join(thought_buf).strip()
                    thought_buf = []
                    # An empty summary does NOT mean success — never imply it did.
                    # Say plainly that nothing was reported back.
                    self.result = final or "(agent finished without a summary)"
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

    async def _memory_preamble(self) -> str:
        """Pull relevant long-term facts for the task and format them for the prompt."""
        if self.semantic is None or self.embedder is None or self.user_uuid is None:
            return ""
        try:
            emb = await self.embedder.embed(self.task)
            rows = await self.semantic.search(self.user_uuid, emb, k=5)
        except Exception as e:
            log.warning("terminal_agent.memory_failed", agent_id=self.id, err=str(e))
            return ""
        if not rows:
            return ""
        facts = "\n".join(f"- {r.content}" for r in rows)
        return f"\n\nRelevant context from Karnveer's memory:\n{facts}"

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
