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
        ],
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "describe":
            snapshot = await asyncio.to_thread(describe_self)
            log.info("self.describe", branch=snapshot.get("branch"), clean=snapshot.get("clean"))
            return snapshot
        return f"error: unknown self action {action!r}"
