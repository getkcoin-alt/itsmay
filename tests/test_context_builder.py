"""build_messages — prompt assembly, incl. the PLAYBOOKS (procedural) block."""

from __future__ import annotations

from core.brain.context_builder import build_messages


def _system(msgs) -> str:
    return msgs[0].content


def test_playbooks_block_rendered_and_distinct_from_facts():
    msgs = build_messages(
        self_context="ctx",
        retrieved_memories=["a stored fact"],
        recent_messages=[],
        user_input="hi",
        playbooks=["PLAYBOOK: Email payouts\nWhen: send payouts\nSteps:\n1. do it"],
    )
    sys = _system(msgs)
    assert "## RELEVANT MEMORIES" in sys and "a stored fact" in sys
    assert "## PLAYBOOKS" in sys and "PLAYBOOK: Email payouts" in sys
    # Distinct blocks, facts above playbooks.
    assert sys.index("## RELEVANT MEMORIES") < sys.index("## PLAYBOOKS")
    assert msgs[-1].content == "hi"  # user turn is last


def test_playbooks_block_omitted_when_empty():
    msgs = build_messages(
        self_context="ctx",
        retrieved_memories=[],
        recent_messages=[],
        user_input="hi",
    )
    assert "## PLAYBOOKS" not in _system(msgs)
