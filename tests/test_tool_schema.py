"""#16 tool-schema compaction + #15 per-model client cache lock."""

from __future__ import annotations

import asyncio

from core.agents import runner
from core.agents.registry import get_agent_registry
from core.brain.orchestrator import Orchestrator
from core.connectors.base import compact_description
from core.connectors.registry import get_registry

_CAP = 205  # compact_description caps ~200 (+1 for the ellipsis)


# ── compact_description (#16) ─────────────────────────────────────────


def test_compact_keeps_short_descriptions():
    assert compact_description("Short and sweet.") == "Short and sweet."
    # Whitespace is collapsed.
    assert compact_description("a\n  b   c") == "a b c"


def test_compact_trims_at_sentence_boundary():
    desc = "First sentence carries the intent. " + "Filler. " * 40
    out = compact_description(desc)
    assert len(out) <= _CAP
    assert out.startswith("First sentence carries the intent.")
    assert out.endswith(".")  # whole sentences, no dangling fragment


def test_compact_hard_caps_when_no_boundary():
    out = compact_description("word " * 100)  # no sentence punctuation
    assert len(out) <= _CAP
    assert out.endswith("…")


# ── registry compact payload (#16) ────────────────────────────────────


def test_connector_tools_openai_compact_is_smaller():
    reg = get_registry()
    full = reg.tools_openai(compact=False)
    compact = reg.tools_openai(compact=True)

    # Same tools + parameters, only descriptions shrink.
    assert [t["function"]["name"] for t in full] == [t["function"]["name"] for t in compact]
    assert [t["function"]["parameters"] for t in full] == [
        t["function"]["parameters"] for t in compact
    ]
    full_chars = sum(len(t["function"]["description"]) for t in full)
    compact_chars = sum(len(t["function"]["description"]) for t in compact)
    assert compact_chars < full_chars  # a real reduction
    assert all(len(t["function"]["description"]) <= _CAP for t in compact)


def test_orchestrator_payload_is_compacted():
    orch = Orchestrator(get_registry(), get_agent_registry(), voice_mode=True)
    payload = orch.tools_payload()
    assert payload  # non-empty
    assert all(len(t["function"]["description"]) <= _CAP for t in payload)


def test_compaction_does_not_touch_approval_enforcement():
    # requires_approval is enforced structurally at Registry.invoke, never from the
    # description — so trimming descriptions can't weaken the gate.
    tool = get_registry().get_tool("self.apply_change")
    assert tool is not None and tool.spec.requires_approval is True
    # And it isn't even sent to the model (not in the payload) — orthogonal.
    payload = get_registry().tools_openai(compact=True)
    apply = next(t for t in payload if t["function"]["name"] == "self.apply_change")
    assert "requires_approval" not in apply["function"]


# ── per-model client cache lock (#15) ─────────────────────────────────


async def test_client_cache_builds_one_per_model_under_concurrency(monkeypatch):
    built: list[str] = []

    class FakeLLM:
        def __init__(self, *, model):
            built.append(model)
            self.model = model

    monkeypatch.setattr(runner, "LLMClient", FakeLLM)
    monkeypatch.setattr(runner, "_model_clients", {})

    async def get(m):
        return runner._client_for_model(m)

    results = await asyncio.gather(*[get("cheap") for _ in range(12)])
    assert len({id(r) for r in results}) == 1  # exactly one client, no leaked losers
    assert built.count("cheap") == 1

    other = runner._client_for_model("heavy")
    assert other is not results[0]  # distinct model → distinct client
