"""Tests for the generic multi-turn tool loop."""

from __future__ import annotations

from core.brain.agent_loop import (
    ClientToolCall,
    Done,
    Token,
    ToolResult,
    run_tool_loop,
)
from core.brain.llm import (
    Message,
    assistant_tool_call_message,
    tool_result_message,
)
from core.connectors.base import InvocationContext
from tests.fakes import FakeLLM, FakeRouter


async def _collect(llm, router, *, max_iters=4):
    msgs = [Message(role="user", content="hi")]
    events = []
    async for ev in run_tool_loop(
        llm=llm, messages=msgs, router=router, ctx=InvocationContext(), max_iters=max_iters
    ):
        events.append(ev)
    return events, msgs


def _text(events) -> str:
    return "".join(e.text for e in events if isinstance(e, Token))


async def test_plain_answer_no_tools():
    llm = FakeLLM([{"text": "hello there"}])
    router = FakeRouter()
    events, _ = await _collect(llm, router)

    assert _text(events) == "hello there"
    done = events[-1]
    assert isinstance(done, Done)
    assert done.stop_reason == "stop"
    assert done.iterations == 1
    assert done.prompt_tokens == 10 and done.completion_tokens == 5
    assert router.executed == []
    assert len(llm.calls) == 1


async def test_server_tool_then_answer_feeds_result_back():
    llm = FakeLLM(
        [
            {
                "text": "checking",
                "tool_calls": [{"id": "c1", "name": "memory.search", "arguments": {"query": "x"}}],
            },
            {"text": "found it"},
        ]
    )
    router = FakeRouter(results={"memory.search": "RESULT_ROWS"})
    events, msgs = await _collect(llm, router)

    # Both passes' visible text is streamed.
    assert _text(events) == "checkingfound it"
    # The server tool ran exactly once with the model's args.
    assert router.executed == [("memory.search", {"query": "x"})]
    # A ToolResult event was surfaced.
    assert any(isinstance(e, ToolResult) and e.name == "memory.search" for e in events)
    # Two model passes happened.
    assert len(llm.calls) == 2
    # The second pass saw the assistant tool-call turn + the tool result turn.
    second = llm.calls[1]["messages"]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in second)
    tool_turns = [m for m in second if m["role"] == "tool"]
    assert tool_turns and tool_turns[0]["content"] == "RESULT_ROWS"
    assert tool_turns[0]["tool_call_id"] == "c1"
    done = events[-1]
    assert isinstance(done, Done) and done.iterations == 2 and done.stop_reason == "stop"


async def test_client_tool_is_fire_and_forget():
    llm = FakeLLM(
        [
            {
                "text": "opening chrome",
                "tool_calls": [
                    {"id": "m1", "name": "mac.open_app", "arguments": {"name": "Google Chrome"}}
                ],
            }
        ]
    )
    router = FakeRouter(client_tools={"mac.open_app"})
    events, _ = await _collect(llm, router)

    # Client tool surfaced as an event, never executed server-side.
    client_events = [e for e in events if isinstance(e, ClientToolCall)]
    assert len(client_events) == 1
    assert client_events[0].name == "mac.open_app"
    assert client_events[0].arguments == {"name": "Google Chrome"}
    assert router.executed == []
    # No second pass — we don't wait on the client.
    assert len(llm.calls) == 1
    assert isinstance(events[-1], Done) and events[-1].stop_reason == "stop"


async def test_max_iters_cap():
    # Every pass calls a server tool, so only the cap stops the loop.
    looping = {
        "text": "again",
        "tool_calls": [{"id": "c", "name": "memory.search", "arguments": {}}],
    }
    llm = FakeLLM([dict(looping) for _ in range(10)])
    router = FakeRouter(results={"memory.search": "r"})
    events, _ = await _collect(llm, router, max_iters=2)

    done = events[-1]
    assert isinstance(done, Done)
    assert done.stop_reason == "max_iters"
    assert done.iterations == 2
    assert len(router.executed) == 2
    assert len(llm.calls) == 2


def test_message_tool_call_shapes():
    # Assistant tool-call turn serializes arguments to a JSON string.
    m = assistant_tool_call_message(
        [{"id": "c1", "name": "memory.save", "arguments": {"content": "fact"}}],
        content="saving",
    )
    d = m.to_dict()
    assert d["role"] == "assistant"
    assert d["content"] == "saving"
    tc = d["tool_calls"][0]
    assert tc["id"] == "c1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "memory.save"
    assert tc["function"]["arguments"] == '{"content": "fact"}'

    # Tool-result turn carries id + name.
    tr = tool_result_message("c1", "memory.save", "saved").to_dict()
    assert tr == {"role": "tool", "content": "saved", "tool_call_id": "c1", "name": "memory.save"}

    # Plain message stays minimal.
    assert Message(role="user", content="hi").to_dict() == {"role": "user", "content": "hi"}
