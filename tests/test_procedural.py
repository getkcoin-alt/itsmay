"""Workflow miner (procedural memory) — distilling tool traces into playbooks.

Fully offline: FakeLLM returns the "extracted playbooks" JSON; the episodic
source, embedder, and semantic sink are in-memory fakes. No DB, no network.
"""

from __future__ import annotations

import json
from uuid import uuid4

import numpy as np

from core.memory.procedural import format_playbook, mine_workflows
from tests.fakes import FakeLLM

_USER = uuid4()


def _trace(goal: str, *tools: str) -> str:
    return json.dumps({"goal": goal, "steps": [{"tool": t, "ok": True} for t in tools]})


class FakeEpisodic:
    def __init__(self, traces: list[str]) -> None:
        self._traces = traces

    async def recent_tool_traces(self, user_id, *, limit: int = 200) -> list[str]:
        return list(self._traces)[:limit]


class FakeEmbedder:
    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed(self, text: str) -> np.ndarray:
        self.embedded.append(text)
        return np.zeros(384, dtype=np.float32)


class RecordingSemantic:
    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.written: list[dict] = []
        self._existing = set(existing)

    async def content_exists(self, user_id, content: str) -> bool:
        return content in self._existing

    async def write(self, user_id, kind, content, embedding, *, source=None, importance=0.5):
        self.written.append(
            {"kind": kind, "content": content, "source": source, "importance": importance}
        )
        return uuid4()


_PAYOUT_TRACES = [
    _trace("send the weekly payout summary", "memory.search", "gmail.send"),
    _trace("email this week's payouts", "memory.search", "gmail.send"),
    _trace("mail the payout numbers", "memory.search", "gmail.send"),
]

_ONE_PLAYBOOK = json.dumps(
    [
        {
            "name": "Email the weekly payout summary",
            "trigger": "send the weekly payout summary",
            "steps": ["memory.search — pull the figures", "gmail.send — email them"],
            "importance": 0.75,
        }
    ]
)


def test_format_playbook_is_self_contained():
    text = format_playbook("Do X", "when Y", ["a.b — first", "c.d — then"])
    assert text == "PLAYBOOK: Do X\nWhen: when Y\nSteps:\n1. a.b — first\n2. c.d — then"


async def test_mines_recurring_traces_into_a_procedural_memory():
    llm = FakeLLM([{"text": _ONE_PLAYBOOK}])
    sem = RecordingSemantic()
    emb = FakeEmbedder()
    out = await mine_workflows(llm, sem, emb, FakeEpisodic(_PAYOUT_TRACES), _USER)

    assert out == {"traces": 3, "playbooks": 1, "saved": 1, "skipped": 0}
    assert len(sem.written) == 1
    w = sem.written[0]
    assert w["kind"] == "procedural"
    assert w["source"] == "procedural-miner"
    assert w["importance"] == 0.75
    assert w["content"].startswith("PLAYBOOK: Email the weekly payout summary")
    # The trigger (goal-like) is what gets embedded, so retrieval matches goals.
    assert emb.embedded == ["send the weekly payout summary"]


async def test_too_few_traces_skips_the_llm_entirely():
    llm = FakeLLM([{"text": _ONE_PLAYBOOK}])
    sem = RecordingSemantic()
    out = await mine_workflows(llm, sem, FakeEmbedder(), FakeEpisodic(_PAYOUT_TRACES[:2]), _USER)

    assert out == {"traces": 2, "playbooks": 0, "saved": 0, "skipped": 0}
    assert sem.written == []
    assert llm.calls == []  # never bothered the model


async def test_idempotent_skips_already_stored_playbook():
    content = format_playbook(
        "Email the weekly payout summary",
        "send the weekly payout summary",
        ["memory.search — pull the figures", "gmail.send — email them"],
    )
    llm = FakeLLM([{"text": _ONE_PLAYBOOK}])
    sem = RecordingSemantic(existing=(content,))
    out = await mine_workflows(llm, sem, FakeEmbedder(), FakeEpisodic(_PAYOUT_TRACES), _USER)

    assert out["saved"] == 0 and out["skipped"] == 1
    assert sem.written == []


async def test_tolerates_non_json_model_output():
    llm = FakeLLM([{"text": "sorry, I couldn't find a pattern"}])
    sem = RecordingSemantic()
    out = await mine_workflows(llm, sem, FakeEmbedder(), FakeEpisodic(_PAYOUT_TRACES), _USER)

    assert out == {"traces": 3, "playbooks": 0, "saved": 0, "skipped": 0}
    assert sem.written == []


async def test_skips_malformed_playbook_entries():
    bad = json.dumps([{"trigger": "no name or steps"}, {"name": "X", "steps": []}])
    llm = FakeLLM([{"text": bad}])
    sem = RecordingSemantic()
    out = await mine_workflows(llm, sem, FakeEmbedder(), FakeEpisodic(_PAYOUT_TRACES), _USER)

    assert out["saved"] == 0 and out["skipped"] == 2
    assert sem.written == []


async def test_empty_array_when_no_pattern():
    llm = FakeLLM([{"text": "[]"}])
    sem = RecordingSemantic()
    out = await mine_workflows(llm, sem, FakeEmbedder(), FakeEpisodic(_PAYOUT_TRACES), _USER)

    assert out == {"traces": 3, "playbooks": 0, "saved": 0, "skipped": 0}
