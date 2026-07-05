"""#13 — `requires_approval` is ENFORCED server-side, not advisory.

A tool flagged `requires_approval` (gmail.send, mac.run_applescript) must never
execute unless the operator named it in the request's `approved_tools`. Enforced
at two layers:

- `Registry.invoke` — the choke point every server execution funnels through
  (orchestrator AND expert sub-agents), returns an `approval_required:` error.
- `Orchestrator.execute_server` — raises `ApprovalRequiredError` so the loop
  emits a typed `ApprovalRequired` event clients can render as a confirm prompt.
"""

from __future__ import annotations

from core.agents.registry import AgentRegistry
from core.brain.agent_loop import ApprovalRequired, ApprovalRequiredError, Done, run_tool_loop
from core.brain.llm import Message
from core.brain.orchestrator import Orchestrator
from core.connectors.base import (
    Connector,
    ConnectorManifest,
    InvocationContext,
    ToolSpec,
)
from core.connectors.registry import Registry
from tests.fakes import FakeLLM


class _SideEffectConnector(Connector):
    """One safe tool + one guarded tool; records real executions."""

    manifest = ConnectorManifest(
        name="fx",
        tools=[
            ToolSpec(name="peek", description="read-only"),
            ToolSpec(
                name="fire",
                description="sends something irreversible",
                requires_approval=True,
                side_effects=["fx.fire"],
            ),
        ],
    )

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> str:
        self.executed.append((action, args))
        return f"{action} done"


def _registry() -> tuple[Registry, _SideEffectConnector]:
    reg = Registry()
    conn = _SideEffectConnector()
    reg.register(conn)
    return reg, conn


# ── Registry.invoke: the deep choke point ────────────────────────────────


async def test_registry_blocks_unapproved_tool_and_never_executes():
    reg, conn = _registry()
    res = await reg.invoke("fx.fire", {"to": "x"}, InvocationContext())
    assert res["ok"] is False
    assert res["error"].startswith("approval_required:")
    assert "fx.fire" in res["error"]
    assert conn.executed == []  # the connector was never touched


async def test_registry_runs_tool_when_approved_for_this_request():
    reg, conn = _registry()
    ctx = InvocationContext(approved_tools=frozenset({"fx.fire"}))
    res = await reg.invoke("fx.fire", {"to": "x"}, ctx)
    assert res == {"ok": True, "result": "fire done"}
    assert conn.executed == [("fire", {"to": "x"})]


async def test_registry_leaves_unflagged_tools_alone():
    reg, conn = _registry()
    res = await reg.invoke("fx.peek", {}, InvocationContext())
    assert res["ok"] is True
    assert conn.executed == [("peek", {})]


# ── Orchestrator: typed signal for the loop ──────────────────────────────


def _orchestrator(reg: Registry) -> Orchestrator:
    return Orchestrator(reg, AgentRegistry(reg, experts=()))


async def test_orchestrator_raises_typed_approval_error():
    reg, conn = _registry()
    orch = _orchestrator(reg)
    try:
        await orch.execute_server("fx.fire", {"to": "x"}, InvocationContext())
        raised = False
    except ApprovalRequiredError as e:
        raised = True
        assert e.tool == "fx.fire" and e.arguments == {"to": "x"}
    assert raised
    assert conn.executed == []


async def test_orchestrator_executes_when_approved():
    reg, conn = _registry()
    orch = _orchestrator(reg)
    ctx = InvocationContext(approved_tools=frozenset({"fx.fire"}))
    out = await orch.execute_server("fx.fire", {"to": "x"}, ctx)
    assert out == "fire done"
    assert conn.executed == [("fire", {"to": "x"})]


# ── Full loop: blocked call → ApprovalRequired event, model told, no execute ─


async def test_loop_emits_approval_required_and_model_sees_blocked():
    reg, conn = _registry()
    orch = _orchestrator(reg)
    llm = FakeLLM(
        [
            {
                "text": "sending now",
                "tool_calls": [{"id": "c1", "name": "fx.fire", "arguments": {"to": "x"}}],
            },
            {"text": "I need your approval first."},
        ]
    )
    msgs = [Message(role="user", content="fire it")]
    events = []
    async for ev in run_tool_loop(
        llm=llm, messages=msgs, router=orch, ctx=InvocationContext(), max_iters=4
    ):
        events.append(ev)

    # The typed event surfaced with the exact call the user must confirm.
    appr = [e for e in events if isinstance(e, ApprovalRequired)]
    assert len(appr) == 1
    assert appr[0].name == "fx.fire" and appr[0].arguments == {"to": "x"}
    # Nothing executed.
    assert conn.executed == []
    # The model's second pass saw the blocked tool result (so it can ask).
    tool_turns = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_turns and tool_turns[0]["content"].startswith("blocked: fx.fire")
    # The loop still finished normally.
    assert isinstance(events[-1], Done) and events[-1].stop_reason == "stop"


async def test_loop_executes_approved_tool_without_event():
    reg, conn = _registry()
    orch = _orchestrator(reg)
    llm = FakeLLM(
        [
            {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "fx.fire", "arguments": {"to": "x"}}],
            },
            {"text": "sent."},
        ]
    )
    ctx = InvocationContext(approved_tools=frozenset({"fx.fire"}))
    msgs = [Message(role="user", content="fire it — approved")]
    events = []
    async for ev in run_tool_loop(llm=llm, messages=msgs, router=orch, ctx=ctx, max_iters=4):
        events.append(ev)

    assert not any(isinstance(e, ApprovalRequired) for e in events)
    assert conn.executed == [("fire", {"to": "x"})]
