"""IM-1.1 honest mode — capability-honesty rules must stay in the prompts.

These guard against the 'it said it did it but nothing happened' regression:
Scrappy must refuse to fake non-shell-doable tasks and never claim done without
proof.
"""

from __future__ import annotations

from core.brain.context_builder import load_system_prompt
from core.terminal_agent.agent import _SYSTEM


def test_main_prompt_has_capability_honesty():
    p = load_system_prompt(voice_mode=False).lower()
    assert "honest" in p
    assert "proof" in p or "verifiable" in p
    assert "shopify" in p  # the canonical non-shell-doable example


def test_voice_prompt_has_capability_honesty():
    p = load_system_prompt(voice_mode=True).lower()
    assert "honest" in p
    assert "shopify" in p


def test_terminal_agent_system_has_honesty_rule():
    s = _SYSTEM.lower()
    assert "honest" in s
    assert "fake" in s  # "never fake success"


def test_agent_result_fallback_is_not_a_fake_completed():
    # The dishonest "(completed)" default is gone; an empty summary must read as
    # "no summary", not as success.
    import inspect

    from core.terminal_agent import agent

    src = inspect.getsource(agent.TerminalAgent._run)
    assert '"(completed)"' not in src
    assert "without a summary" in src
