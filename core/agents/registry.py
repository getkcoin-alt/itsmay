"""Agent registry — which experts exist, and how to reach them.

Holds the expert roster and filters it down to those whose required connector
namespaces are all installed (no point offering Scrappy an `ask_email_expert`
tool if there's no email connector behind it). Produces the `ask_<name>`
delegation tools Scrappy sees, and dispatches a delegation call to the runner.
"""

from __future__ import annotations

from core.agents.base import SubAgentResult, SubAgentSpec
from core.agents.experts import ALL_EXPERTS
from core.agents.runner import run_subagent
from core.connectors.base import InvocationContext, compact_description
from core.connectors.registry import Registry, get_registry
from core.logging import get_logger

log = get_logger(__name__)

_TOOL_PREFIX = "ask_"


class AgentRegistry:
    def __init__(self, connectors: Registry, experts: tuple[SubAgentSpec, ...] = ALL_EXPERTS):
        self._connectors = connectors
        installed = set(connectors.connectors.keys())
        self._available: dict[str, SubAgentSpec] = {}
        for spec in experts:
            missing = [ns for ns in spec.tool_namespaces if ns not in installed]
            if missing:
                log.info("agents.expert_unavailable", expert=spec.name, missing=missing)
                continue
            self._available[spec.tool_name] = spec
        log.info("agents.ready", available=sorted(self._available))

    # ── introspection ────────────────────────────────────────────
    @property
    def available(self) -> list[SubAgentSpec]:
        return list(self._available.values())

    def has(self, tool_name: str) -> bool:
        return tool_name in self._available

    def tools_openai(self, *, compact: bool = False) -> list[dict]:
        """Delegation tools Scrappy can call — one `ask_<expert>` per expert.

        `compact=True` trims the description for the LLM payload (#16)."""
        out: list[dict] = []
        for spec in self._available.values():
            description = (
                f"Delegate to the {spec.title}, expert in: {spec.expertise} "
                "Hand off a clear, self-contained task; you get back a "
                "synthesized answer to relay or act on. Do NOT call this for "
                "greetings, chat, opinions, or questions about your own "
                "capabilities — answer those yourself."
            )
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.tool_name,
                        "description": (
                            compact_description(description) if compact else description
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": (
                                        "A complete, standalone instruction for the "
                                        "expert. Include all context it needs — it "
                                        "cannot see the conversation."
                                    ),
                                }
                            },
                            "required": ["task"],
                        },
                    },
                }
            )
        return out

    # ── dispatch ─────────────────────────────────────────────────
    async def run(self, tool_name: str, args: dict, ctx: InvocationContext) -> SubAgentResult:
        spec = self._available.get(tool_name)
        if spec is None:
            return SubAgentResult(agent=tool_name, text=f"no such expert: {tool_name}")
        task = str(args.get("task", "")).strip()
        if not task:
            return SubAgentResult(agent=spec.name, text="error: empty task")
        if ctx.llm is None:
            return SubAgentResult(agent=spec.name, text="error: no LLM available to the expert")
        log.info("agents.delegate", expert=spec.name, task=task[:160])
        return await run_subagent(
            spec, task, llm=ctx.llm, registry=self._connectors, ctx=ctx
        )


_agent_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Idempotent singleton, built over the connector registry."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry(get_registry())
    return _agent_registry
