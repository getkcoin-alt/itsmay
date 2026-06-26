"""Terminal Agent connector — Scrappy's tools to spawn and check bash workers."""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.terminal_agent.registry import get_agent_store


class TerminalConnector(Connector):
    manifest = ConnectorManifest(
        name="terminal",
        version="0.1.0",
        description="Spawn and manage Claude terminal worker agents with bash access.",
        tools=[
            ToolSpec(
                name="spawn",
                description=(
                    "Spawn a new Claude terminal agent that completes a task autonomously using bash. "
                    "Best for: writing/running code, file processing, git operations, data tasks, "
                    "installing packages, running tests. Give it a self-contained task description. "
                    "Returns agent_id — use terminal.status to poll until done."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Clear, self-contained task for the terminal agent.",
                        }
                    },
                    "required": ["task"],
                },
                executor="server",
            ),
            ToolSpec(
                name="status",
                description=(
                    "Get current status and output of a terminal agent. "
                    "status is one of: pending | running | done | error. "
                    "Returns result and recent log entries (cmd/output/thought/result)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent ID returned by terminal.spawn",
                        }
                    },
                    "required": ["agent_id"],
                },
                executor="server",
            ),
            ToolSpec(
                name="list",
                description="List recent terminal agents (newest first) with their status and task.",
                parameters={"type": "object", "properties": {}, "required": []},
                executor="server",
            ),
        ],
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        store = get_agent_store()

        if action == "spawn":
            task = (args.get("task") or "").strip()
            if not task:
                return {"ok": False, "error": "task is required"}
            if ctx.llm is None:
                return {"ok": False, "error": "LLM not available in context"}
            agent = store.spawn(
                task,
                ctx.llm,
                embedder=ctx.embedder,
                semantic=ctx.semantic,
                user_uuid=ctx.user_uuid,
            )
            return {
                "agent_id": agent.id,
                "status": agent.status,
                "work_dir": agent.work_dir,
                "message": (
                    f"Terminal agent {agent.id} spawned and running. "
                    f"Call terminal.status('{agent.id}') to check progress and get results."
                ),
            }

        if action == "status":
            agent_id = (args.get("agent_id") or "").strip()
            agent = store.get(agent_id)
            if not agent:
                return {"ok": False, "error": f"no agent with id {agent_id!r}"}
            d = agent.to_dict(full=True)
            if isinstance(d.get("log"), list):
                d["log"] = d["log"][-30:]
            return d

        if action == "list":
            return [a.to_dict() for a in store.list_all()[:20]]

        return {"ok": False, "error": f"unknown action: {action!r}"}
