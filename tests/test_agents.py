"""Tests for the sub-agent framework: availability, delegation, orchestration."""

from __future__ import annotations

from core.agents.registry import AgentRegistry
from core.agents.runner import run_subagent
from core.brain.orchestrator import Orchestrator
from core.config import get_settings
from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.connectors.registry import Registry
from tests.fakes import FakeLLM


def _make_connector(name: str, tools: list[tuple[str, str, str]]) -> Connector:
    """tools: list of (tool_name, executor, canned_result)."""

    class _Stub(Connector):
        manifest = ConnectorManifest(
            name=name,
            tools=[ToolSpec(name=t, description=t, executor=ex) for (t, ex, _) in tools],
        )

        async def invoke(self, action: str, args: dict, ctx: InvocationContext):
            for tname, _ex, result in tools:
                if tname == action:
                    return result
            return f"unknown {action}"

    return _Stub()


def _registry(*connectors: Connector) -> Registry:
    reg = Registry()
    for c in connectors:
        reg.register(c)
    return reg


# ── availability filtering ───────────────────────────────────────
def test_expert_unavailable_without_its_connector():
    agents = AgentRegistry(_registry())  # no connectors at all
    names = {s.tool_name for s in agents.available}
    # Strategist needs no tools → always available.
    assert "ask_strategist" in names
    # Memory Keeper needs the `memory` connector → unavailable here.
    assert "ask_memory_keeper" not in names
    assert agents.has("ask_strategist")
    assert not agents.has("ask_memory_keeper")


def test_expert_becomes_available_when_connector_present():
    reg = _registry(_make_connector("memory", [("save", "server", "saved!")]))
    agents = AgentRegistry(reg)
    names = {s.tool_name for s in agents.available}
    assert {"ask_strategist", "ask_memory_keeper"} <= names

    tools = agents.tools_openai()
    tool_names = {t["function"]["name"] for t in tools}
    assert {"ask_strategist", "ask_memory_keeper"} <= tool_names
    # Delegation tools take a single `task` arg.
    mk = next(t for t in tools if t["function"]["name"] == "ask_memory_keeper")
    assert mk["function"]["parameters"]["required"] == ["task"]


# ── running an expert ────────────────────────────────────────────
async def test_strategist_pure_reasoning():
    llm = FakeLLM([{"text": "Ship the API first, then automate."}])
    from core.agents.experts import STRATEGIST

    result = await run_subagent(
        STRATEGIST,
        "Should I build API or UI first?",
        llm=llm,
        registry=Registry(),
        ctx=InvocationContext(),
    )
    assert result.text == "Ship the API first, then automate."
    assert result.tools_used == []
    assert result.iterations == 1
    # The expert's own system prompt + the task framed it.
    sys_msg = llm.calls[0]["messages"][0]
    assert sys_msg["role"] == "system" and "Strategist" in sys_msg["content"]
    assert llm.calls[0]["messages"][1]["content"] == "Should I build API or UI first?"


async def test_memory_keeper_uses_only_its_namespace_and_saves():
    reg = _registry(
        _make_connector("memory", [("save", "server", "saved!")]),
        _make_connector("other", [("thing", "server", "nope")]),
    )
    # Memory Keeper is a tool-using expert → it now runs on the cheap agent model
    # (IM-2.3). Give the fake that model so the auto-routing is a no-op and this
    # scripted client is the one actually used.
    llm = FakeLLM(
        [
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "memory.save",
                        "arguments": {"content": "Karnveer uses Stripe"},
                    }
                ]
            },
            {"text": "Saved that."},
        ],
        model=get_settings().llm_agent_model,
    )
    from core.agents.experts import MEMORY_KEEPER

    result = await run_subagent(
        MEMORY_KEEPER, "Remember I use Stripe", llm=llm, registry=reg, ctx=InvocationContext()
    )
    assert result.text == "Saved that."
    assert result.tools_used == ["memory.save"]
    assert result.iterations == 2

    # The expert was only shown its own namespace's tools.
    offered = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert offered == {"memory.save"}
    assert "other.thing" not in offered


# ── orchestrator: Scrappy's merged toolbox + routing ─────────────
def test_orchestrator_toolbox_voice_vs_api():
    reg = _registry(
        _make_connector("memory", [("save", "server", "ok")]),
        _make_connector("mac", [("open_app", "client_mac", "")]),
    )
    agents = AgentRegistry(reg)

    api = Orchestrator(reg, agents, voice_mode=False)
    api_names = {t["function"]["name"] for t in api.tools_payload()}
    assert "memory.save" in api_names
    assert "ask_strategist" in api_names
    assert "mac.open_app" not in api_names  # client tools hidden without voice

    voice = Orchestrator(reg, agents, voice_mode=True)
    voice_names = {t["function"]["name"] for t in voice.tools_payload()}
    assert "mac.open_app" in voice_names  # surfaced when voice client is listening

    assert voice.is_client_tool("mac.open_app") is True
    assert voice.is_client_tool("memory.save") is False
    assert voice.is_client_tool("ask_strategist") is False


async def test_orchestrator_routes_server_tool_and_delegation():
    reg = _registry(_make_connector("memory", [("save", "server", "saved!")]))
    agents = AgentRegistry(reg)
    orch = Orchestrator(reg, agents, voice_mode=False)

    # Plain server connector tool.
    out = await orch.execute_server("memory.save", {"content": "x"}, InvocationContext())
    assert out == "saved!"

    # Delegation routes into a sub-agent loop (which here just answers).
    llm = FakeLLM([{"text": "Focus on revenue."}])
    ctx = InvocationContext(llm=llm, registry=reg)
    out = await orch.execute_server("ask_strategist", {"task": "what now?"}, ctx)
    assert out == "Focus on revenue."
