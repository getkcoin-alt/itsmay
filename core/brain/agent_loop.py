"""Generic multi-turn tool-execution loop.

This is the engine that turns a single LLM call into an *agent*: it streams the
model's reply, and when the model emits tool calls it executes the server-side
ones, feeds the results back, and re-invokes the model — looping until the model
stops calling tools (or a hard iteration cap is hit).

The loop is deliberately generic. It knows nothing about Mac tools, connectors,
or sub-agents; it talks to a `ToolRouter` that answers three questions:

    - what tools can the model see?             (`tools_payload`)
    - is this tool executed by a remote client? (`is_client_tool`)
    - run this server tool, give me a string.   (`execute_server`)

Client-executed tools (e.g. `mac.*`, which run on the user's MacBook) can't be
awaited inside one request, so they're emitted as fire-and-forget events and are
*not* fed back into the model. Everything else is executed in-process and its
result is appended to the running transcript so the model can react to it.

Consumers (the chat router, the sub-agent runner) iterate the async event
stream and do what they like with each event — stream tokens over SSE, collect
the final text, etc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from core.brain.llm import (
    LLMClient,
    Message,
    assistant_tool_call_message,
    tool_result_message,
)
from core.connectors.base import InvocationContext
from core.logging import get_logger

log = get_logger(__name__)


# ── events the loop emits ────────────────────────────────────────
@dataclass(slots=True)
class Token:
    """A chunk of visible assistant text, streamed live as the model emits it."""

    text: str


@dataclass(slots=True)
class ClientToolCall:
    """A tool the *client* must run (fire-and-forget; not fed back to the model)."""

    id: str
    name: str
    arguments: dict


@dataclass(slots=True)
class ToolResult:
    """A server tool finished. Emitted for observability; clients may ignore it."""

    name: str
    summary: str


@dataclass(slots=True)
class ToolStart:
    """A server tool is about to run. Emitted right before execute_server is called."""

    id: str
    name: str


@dataclass(slots=True)
class Done:
    """Terminal event. Carries token accounting and why the loop stopped."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    iterations: int = 0
    stop_reason: str = "stop"  # "stop" | "max_iters"


LoopEvent = Token | ClientToolCall | ToolStart | ToolResult | Done


class ToolRouter(Protocol):
    """What the loop needs from whatever owns the tools."""

    def tools_payload(self) -> list[dict]:
        """OpenAI-style `tools=[...]` payload, or [] for none."""
        ...

    def is_client_tool(self, name: str) -> bool:
        """True if this tool runs on a remote client (don't execute or feed back)."""
        ...

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        """Run a server-side tool and return a string result for the model."""
        ...


async def run_tool_loop(
    *,
    llm: LLMClient,
    messages: list[Message],
    router: ToolRouter,
    ctx: InvocationContext,
    temperature: float = 0.7,
    max_iters: int = 4,
) -> AsyncIterator[LoopEvent]:
    """Drive the model through tool calls until it produces a final answer.

    `messages` is mutated in place as the transcript grows (assistant tool-call
    turns and tool-result turns are appended). Pass a copy if you need the
    original preserved.
    """
    tools = router.tools_payload() or None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    for iteration in range(1, max_iters + 1):
        # ── one streamed model pass ──────────────────────────────
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        # Hold the generator explicitly so we can close it deterministically.
        # Breaking out of `async for` on the done-chunk leaves the underlying
        # httpx response OPEN until lazy GC finalization. On the next pass a
        # second stream opens on the same client and the connection pool trips
        # over the half-closed one — surfacing as "Attempted to read or stream
        # content, but the stream has been closed" (and it compounds across
        # nested expert loops sharing one client). `aclose()` forces the
        # response cleanup to run now, in this task, before the next pass.
        stream = llm.chat_stream(messages, temperature=temperature, tools=tools)
        try:
            async for chunk in stream:
                if chunk.delta:
                    text_parts.append(chunk.delta)
                    yield Token(chunk.delta)  # stream live
                if chunk.done:
                    if chunk.prompt_eval_count is not None:
                        prompt_tokens = chunk.prompt_eval_count
                    if chunk.eval_count is not None:
                        completion_tokens = chunk.eval_count
                    tool_calls = chunk.tool_calls or []
                    break
        finally:
            await stream.aclose()
        pass_text = "".join(text_parts)

        if not tool_calls:
            yield Done(prompt_tokens, completion_tokens, iteration, "stop")
            return

        # ── split client (fire-and-forget) vs server (execute + feed back) ──
        client_calls = [c for c in tool_calls if router.is_client_tool(c.get("name", ""))]
        server_calls = [c for c in tool_calls if not router.is_client_tool(c.get("name", ""))]

        for c in client_calls:
            yield ClientToolCall(
                id=c.get("id") or "",
                name=c.get("name") or "",
                arguments=c.get("arguments") or {},
            )

        if not server_calls:
            # Only client tools requested — nothing to feed back; the client acts.
            yield Done(prompt_tokens, completion_tokens, iteration, "stop")
            return

        # Record the assistant's request, then each tool's result, so the next
        # pass sees the outcomes. Only the server calls go into history so every
        # tool_call has a matching tool result (providers reject mismatches).
        messages.append(assistant_tool_call_message(server_calls, content=pass_text))
        for c in server_calls:
            name = c.get("name") or ""
            yield ToolStart(id=c.get("id") or "", name=name)
            try:
                result = await router.execute_server(name, c.get("arguments") or {}, ctx)
            except Exception as e:  # never let a tool blow up the whole turn
                log.exception("agent_loop.tool_failed", tool=name)
                result = f"error: {type(e).__name__}: {e}"
            messages.append(tool_result_message(c.get("id") or "", name, result))
            yield ToolResult(name=name, summary=result[:200])

    # Fell out of the loop without a tool-free answer.
    yield Done(prompt_tokens, completion_tokens, max_iters, "max_iters")
