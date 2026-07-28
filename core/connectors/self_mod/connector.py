"""Self connector — Scrappy introspecting (and, later, changing) his own code.

Namespace `self` (tools surface to the model as `self.describe`, …). This is the
head of Epic 5: `self.describe` is read-only and always safe; the write tools
(`self.propose_change`, `self.apply_change`) land next and are gated by the
guardrails (`core/identity/self_guard.py`) + the server-side approval system.

`self.propose_change` will hand implementation to Claude Code on the Mac via the
same worker bridge the `coder` connector uses — Scrappy plans, Claude Code's
hands do the edit on a branch, tests + the operator gate the merge.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.identity.introspect import describe_self
from core.logging import get_logger

log = get_logger(__name__)


class SelfConnector(Connector):
    manifest = ConnectorManifest(
        name="self",
        version="0.1.0",
        description="Introspect and (later) modify Scrappy's own codebase.",
        tools=[
            ToolSpec(
                name="describe",
                description=(
                    "Read-only snapshot of your OWN codebase right now: current git "
                    "branch, whether the working tree is clean, your recent commits, "
                    "top-level structure, which files are PROTECTED from "
                    "self-modification, and whether self-modification is enabled or "
                    "frozen. Call this before reasoning about changing yourself."
                ),
                parameters={"type": "object", "properties": {}},
                executor="server",
            ),
            ToolSpec(
                name="propose_change",
                description=(
                    "Propose a change to your OWN code. Hand the goal to Claude Code "
                    "on the Mac, which creates a `scrappy/self-*` branch, implements "
                    "it, and runs the tests — then I guard the diff against the "
                    "protected files and report back the branch, changed files, test "
                    "result, and whether it's safe to apply. This NEVER merges to main "
                    "and needs a connected `scrappy worker`. Applying is a separate, "
                    "approval-gated step. Call self.describe first."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": (
                                "What to change about yourself, as one complete "
                                "instruction (Claude Code can't see this conversation)."
                            ),
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max seconds to wait (60–600, default 480).",
                        },
                    },
                    "required": ["goal"],
                },
                executor="server",
                side_effects=["filesystem.write", "code.execute", "git.branch"],
            ),
            ToolSpec(
                name="apply_change",
                description=(
                    "APPLY a proposal branch to main — this changes your running code. "
                    "I re-guard the branch's real diff, tag the current main as "
                    "last-good, merge it (authored as you), and run the tests; if they "
                    "fail I auto-roll-back. Requires the operator's approval and a "
                    "connected worker. Only call after self.propose_change reported the "
                    "branch is safe to apply."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "The scrappy/self-* branch from self.propose_change.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Why you're making this change (for the audit memory).",
                        },
                    },
                    "required": ["branch"],
                },
                executor="server",
                requires_approval=True,
                side_effects=["git.merge", "code.execute", "self.modify"],
            ),
            ToolSpec(
                name="rollback",
                description=(
                    "Reset main to the last-good tag (the state before the most recent "
                    "apply). Use this if an applied change misbehaves. Restart to run "
                    "the restored version."
                ),
                parameters={"type": "object", "properties": {}},
                executor="server",
                side_effects=["git.reset", "self.modify"],
            ),
        ],
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "describe":
            snapshot = await asyncio.to_thread(describe_self)
            log.info("self.describe", branch=snapshot.get("branch"), clean=snapshot.get("clean"))
            return snapshot
        if action == "propose_change":
            from core.identity.propose import propose_change
            from core.worker.bridge import get_worker_bridge

            result = await propose_change(
                str(args.get("goal", "")),
                bridge=get_worker_bridge(),
                session_id=ctx.session_id,
                timeout=int(args.get("timeout") or 480),
            )
            log.info("self.propose_change", branch=result.branch, ok=result.ok)
            return result.to_dict()
        if action == "apply_change":
            from core.identity.apply import apply_change
            from core.worker.bridge import get_worker_bridge

            res = await apply_change(
                str(args.get("branch", "")),
                notes=str(args.get("notes", "")),
                bridge=get_worker_bridge(),
                semantic=ctx.semantic,
                embedder=ctx.embedder,
                user_id=ctx.user_uuid,
                session_id=ctx.session_id,
            )
            log.info("self.apply_change", branch=res.branch, applied=res.applied)
            return res.to_dict()
        if action == "rollback":
            from core.identity.apply import rollback
            from core.worker.bridge import get_worker_bridge

            res = await rollback(bridge=get_worker_bridge(), session_id=ctx.session_id)
            log.info("self.rollback", ok=res.ok)
            return res.to_dict()
        return f"error: unknown self action {action!r}"
