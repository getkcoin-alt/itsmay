"""IM-2.3 — route tool-using experts to the cheap agent model.

Memory/Email/Researcher do mechanical tool-calling the 8b handles fine, so they
auto-route to `llm_agent_model`. The Strategist is `heavy` (pure reasoning) and
stays on the big model; an explicit `spec.model` always wins; and flipping
`experts_use_agent_model` off forces everything back to the default client.
"""

from __future__ import annotations

import types

import pytest

from core.agents import runner
from core.agents.base import SubAgentSpec
from core.agents.experts import MEMORY_KEEPER, STRATEGIST


class _Default:
    """Stand-in for the orchestrator's 70b client (just needs `.model`)."""

    model = "llama-3.3-70b-versatile"


def _settings(use_cheap: bool = True, agent_model: str = "llama-3.1-8b-instant"):
    return types.SimpleNamespace(
        experts_use_agent_model=use_cheap, llm_agent_model=agent_model
    )


@pytest.fixture(autouse=True)
def _isolate_cache():
    runner._model_clients.clear()
    yield
    runner._model_clients.clear()


def test_tool_expert_routes_to_cheap_model(monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: _settings())
    default = _Default()
    client = runner._expert_llm(MEMORY_KEEPER, default)
    assert client.model == "llama-3.1-8b-instant"
    assert client is not default  # a distinct cheap client, not the 70b


def test_heavy_expert_stays_on_default(monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: _settings())
    default = _Default()
    assert runner._expert_llm(STRATEGIST, default) is default  # never downgraded


def test_routing_disabled_keeps_default(monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: _settings(use_cheap=False))
    default = _Default()
    assert runner._expert_llm(MEMORY_KEEPER, default) is default


def test_explicit_model_override_wins_even_when_routing_off(monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: _settings(use_cheap=False))
    spec = SubAgentSpec(
        name="x", title="X", expertise="e", system_prompt="s", model="some-pinned-model"
    )
    client = runner._expert_llm(spec, _Default())
    assert client.model == "some-pinned-model"


def test_cheap_clients_are_cached_per_model(monkeypatch):
    monkeypatch.setattr(runner, "get_settings", lambda: _settings())
    a = runner._expert_llm(MEMORY_KEEPER, _Default())
    b = runner._expert_llm(MEMORY_KEEPER, _Default())
    assert a is b  # one client per model, reused across delegations


def test_strategist_is_marked_heavy():
    assert STRATEGIST.heavy is True
    assert MEMORY_KEEPER.heavy is False
