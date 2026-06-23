"""Shared test doubles — a scriptable LLM and a minimal tool router."""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.brain.llm import ChatChunk, Message
from core.connectors.base import InvocationContext


class FakeLLM:
    """Yields scripted ChatChunks. Each script step is a dict:

        {"text": "visible reply", "tool_calls": [{"id","name","arguments"}]}

    One step is consumed per `chat_stream` call. Records every call's messages
    (as dicts) and the tools payload for assertions.
    """

    def __init__(self, script: list[dict] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict] = []

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
        **_: object,
    ) -> AsyncIterator[ChatChunk]:
        self.calls.append(
            {
                "messages": [m.to_dict() for m in messages],
                "tools": tools,
                "temperature": temperature,
            }
        )
        step = self.script.pop(0) if self.script else {"text": "", "tool_calls": []}
        text = step.get("text", "")
        if text:
            # Stream in two pieces so token concatenation is exercised.
            mid = max(1, len(text) // 2)
            yield ChatChunk(delta=text[:mid], done=False)
            yield ChatChunk(delta=text[mid:], done=False)
        yield ChatChunk(
            delta="",
            done=True,
            prompt_eval_count=10,
            eval_count=5,
            tool_calls=step.get("tool_calls") or None,
        )


class FakeRouter:
    """Minimal ToolRouter: declares some tools as client-side, returns canned
    results for server tools, and records what it executed."""

    def __init__(
        self,
        *,
        client_tools: set[str] | None = None,
        results: dict[str, str] | None = None,
    ) -> None:
        self.client_tools = client_tools or set()
        self.results = results or {}
        self.executed: list[tuple[str, dict]] = []

    def tools_payload(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "stub"}}]

    def is_client_tool(self, name: str) -> bool:
        return name in self.client_tools

    async def execute_server(self, name: str, args: dict, ctx: InvocationContext) -> str:
        self.executed.append((name, args))
        return self.results.get(name, f"ran {name}")
