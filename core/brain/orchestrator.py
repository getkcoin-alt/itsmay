"""Scrappy's top-level tool router.

Scrappy sees one merged toolbox:
    - delegation tools  (`ask_<expert>`)         → run an expert sub-agent
    - server connectors  (`memory.*`, …)         → run in-process
    - client connectors  (`mac.*`, voice only)   → emitted to the Mac client

This object is the `ToolRouter` the agentic loop drives for the *top* level. It
decides what tools to expose (client tools only in voice mode), classifies each
call, and executes the server-side ones — dispatching delegation calls to the
expert registry and everything else to the connector registry.
"""

from __future__ import annotations

from core.agents.registry import AgentRegistry
from core.brain.agent_loop import ApprovalRequiredError
from core.connectors.base import InvocationContext
from core.connectors.registry import Registry, flatten_result
from core.logging import get_logger

log = get_logger(__name__)


class Orchestrator:
    def __init__(
        self,
        connectors: Registry,
        agents: AgentRegistry,
        *,
        voice_mode: bool = False,
    ) -> None:
        self._connectors = connectors
        self._agents = agents
        self._voice_mode = voice_mode

    # ── ToolRouter surface ───────────────────────────────────────
    def tools_payload(self) -> list[dict]:
        tools: list[dict] = []
        tools.extend(self._agents.tools_openai())  # experts
        tools.extend(self._connectors.tools_openai(executors={"server"}))  # in-process
        if self._voice_mode:
            # Mac control only makes sense when the voice client is listening.
            tools.extend(self._connectors.tools_openai(executors={"client_mac"}))
        return tools

    def is_client_tool(self, name: str) -> bool:
        if self._agents.has(name):
            return False
        executor = self._connectors.executor_for(name)
        return executor is not None and executor != "server"

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        # Delegation → expert sub-agent.
        if self._agents.has(name):
            result = await self._agents.run(name, args, ctx)
            return result.text
        # Approval gate: a `requires_approval` tool never runs without the
        # operator's explicit per-request consent (ctx.approved_tools, set from
        # the authenticated request body). Raising (vs returning an error string)
        # lets the loop emit a typed ApprovalRequired event clients can render.
        rt = self._connectors.get_tool(name)
        if rt is not None and rt.spec.requires_approval and name not in ctx.approved_tools:
            log.warning("orchestrator.approval_blocked", tool=name)
            raise ApprovalRequiredError(name, args)
        # In-process connector tool.
        res = await self._connectors.invoke(name, args, ctx)
        return flatten_result(res)
