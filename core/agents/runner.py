"""Runs a sub-agent: spec + task → synthesized answer.

The runner gives an expert its own restricted view of the toolbox (only the
connector namespaces its spec allows, server-side only — no Mac/client tools,
no delegating onward to other experts) and then drives the generic tool loop to
completion, collecting the final text.

Experts run server-side and synchronously from Scrappy's point of view: he calls
`ask_<expert>`, the runner loops internally, and the final text comes back as the
tool result Scrappy reads.
"""

from __future__ import annotations

from core.agents.base import SubAgentResult, SubAgentSpec
from core.brain.agent_loop import ClientToolCall, Done, Token, ToolResult, run_tool_loop
from core.brain.llm import LLMClient, Message
from core.connectors.base import InvocationContext
from core.connectors.registry import Registry, flatten_result
from core.logging import get_logger

log = get_logger(__name__)


class _SubAgentToolRouter:
    """The expert's toolbox: server tools from its allowed namespaces only."""

    def __init__(self, registry: Registry, namespaces: tuple[str, ...]) -> None:
        self._registry = registry
        self._allowed = set(namespaces)

    def tools_payload(self) -> list[dict]:
        return [
            t
            for t in self._registry.tools_openai(executors={"server"})
            if t["function"]["name"].split(".", 1)[0] in self._allowed
        ]

    def is_client_tool(self, name: str) -> bool:
        # Experts get no client tools at all; everything they can see is server.
        return False

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        # Guard: refuse tools outside the expert's namespaces even if the model
        # hallucinates one.
        if name.split(".", 1)[0] not in self._allowed:
            return f"error: tool {name!r} is not available to this expert"
        res = await self._registry.invoke(name, args, ctx)
        return flatten_result(res)


async def run_subagent(
    spec: SubAgentSpec,
    task: str,
    *,
    llm: LLMClient,
    registry: Registry,
    ctx: InvocationContext,
) -> SubAgentResult:
    """Execute one expert against a task and return its synthesized answer."""
    router = _SubAgentToolRouter(registry, spec.tool_namespaces)
    messages = [
        Message(role="system", content=spec.system_prompt),
        Message(role="user", content=task),
    ]

    text_parts: list[str] = []
    tools_used: list[str] = []
    iterations = 0
    stop_reason = "stop"

    async for ev in run_tool_loop(
        llm=llm,
        messages=messages,
        router=router,
        ctx=ctx,
        temperature=spec.temperature,
        max_iters=spec.max_iters,
    ):
        if isinstance(ev, Token):
            text_parts.append(ev.text)
        elif isinstance(ev, ToolResult):
            tools_used.append(ev.name)
        elif isinstance(ev, ClientToolCall):
            # Shouldn't happen (router exposes no client tools), but ignore safely.
            log.warning("subagent.unexpected_client_tool", agent=spec.name, tool=ev.name)
        elif isinstance(ev, Done):
            iterations = ev.iterations
            stop_reason = ev.stop_reason

    text = "".join(text_parts).strip()
    if not text:
        text = "(the expert finished without a textual answer)"
    log.info(
        "subagent.done",
        agent=spec.name,
        iterations=iterations,
        tools_used=tools_used,
        stop_reason=stop_reason,
    )
    return SubAgentResult(
        agent=spec.name,
        text=text,
        iterations=iterations,
        tools_used=tools_used,
        stop_reason=stop_reason,
    )
