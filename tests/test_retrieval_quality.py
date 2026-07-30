"""Retrieval quality + expert discipline.

Three things this pins down:
  - the similarity floor keeps unrelated memories out of the prompt (both backends);
  - the Postgres search stays INDEX-FRIENDLY (a plain `ORDER BY embedding <=> $vec`
    in stage 1) — the regression that silently turns every search into a seq scan;
  - every expert carries the operating contract, so a vague hand-off can't come
    back as "I'm Scrappy Singh's long-term memory expert…".
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from core.agents.experts import ALL_EXPERTS, OPERATING_CONTRACT
from core.agents.registry import AgentRegistry
from core.connectors.registry import get_registry
from core.memory import semantic as semantic_mod
from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore, ensure_schema


def _vec(*xs: float) -> np.ndarray:
    return np.array(xs, dtype=np.float32)


@pytest.fixture
def stores(tmp_path):
    path = ensure_schema(str(tmp_path / "m.db"))
    return SqliteEpisodicStore(path), SqliteSemanticStore(path)


# ── similarity floor ──────────────────────────────────────────────────


async def test_floor_drops_unrelated_memories(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "on topic", _vec(1, 0, 0, 0))
    await sem.write(uid, "factual", "orthogonal junk", _vec(0, 1, 0, 0))

    # Without a floor, vector search hands back its k nearest rows no matter how
    # unrelated — the junk rides along into the prompt.
    assert len(await sem.search(uid, _vec(1, 0, 0, 0), k=5)) == 2

    hits = await sem.search(uid, _vec(1, 0, 0, 0), k=5, min_similarity=0.25)
    assert [h.content for h in hits] == ["on topic"]


async def test_floor_can_return_nothing(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "unrelated", _vec(0, 1, 0, 0))
    assert await sem.search(uid, _vec(1, 0, 0, 0), k=5, min_similarity=0.5) == []


async def test_floor_defaults_off_for_explicit_searches(stores):
    # `memory.search` and the console are explicit lookups — the operator asked,
    # so a weak hit still beats an empty answer. Only the auto-context path floors.
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "weak match", _vec(0, 1, 0, 0))
    assert len(await sem.search(uid, _vec(1, 0, 0, 0), k=5)) == 1


async def test_floor_preserves_ranking_of_survivors(stores):
    epi, sem = stores
    uid = await epi.get_or_create_user("karnveer")
    await sem.write(uid, "factual", "best", _vec(1, 0, 0, 0))
    await sem.write(uid, "factual", "good", _vec(0.9, 0.1, 0, 0))
    await sem.write(uid, "factual", "junk", _vec(0, 0, 1, 0))

    hits = await sem.search(uid, _vec(1, 0, 0, 0), k=5, min_similarity=0.25)
    assert [h.content for h in hits] == ["best", "good"]


# ── postgres query shape (the seq-scan regression) ────────────────────


def _pg_search_sql() -> str:
    return inspect.getsource(semantic_mod.SemanticStore.search)


def test_pg_search_stage1_is_index_friendly():
    sql = _pg_search_sql()
    # pgvector only uses the ivfflat index for a BARE distance ordering.
    assert "ORDER BY embedding <=> $2" in sql
    # ...and the importance weighting must NOT be inside that ordering, or the
    # planner falls back to scanning + scoring every row in the table.
    assert "ORDER BY (1 - (embedding <=> $2)) * (0.5 + 0.5 * importance)" not in sql


def test_pg_search_reranks_and_floors_in_stage2():
    sql = _pg_search_sql()
    assert "WITH candidates AS" in sql
    assert "ORDER BY similarity * (0.5 + 0.5 * importance) DESC" in sql
    assert "WHERE similarity >= $7" in sql


def test_pg_and_sqlite_search_signatures_match():
    from core.memory.sqlite_store import SqliteSemanticStore as S

    pg = set(inspect.signature(semantic_mod.SemanticStore.search).parameters)
    lite = set(inspect.signature(S.search).parameters)
    assert pg == lite  # backends must stay swappable


def test_candidate_pool_is_larger_than_k():
    from core.config import get_settings

    assert get_settings().retrieval_candidate_fanout >= 2
    assert 0.0 < get_settings().retrieval_min_similarity < 1.0


# ── expert discipline ─────────────────────────────────────────────────


@pytest.mark.parametrize("spec", ALL_EXPERTS, ids=lambda s: s.name)
def test_every_expert_carries_the_operating_contract(spec):
    assert spec.system_prompt.startswith(OPERATING_CONTRACT)


def test_contract_forbids_self_description():
    # The exact failure seen in the terminal: the expert answered with its own
    # job description instead of doing the task.
    low = OPERATING_CONTRACT.lower()
    assert "never introduce" in low and "describe yourself" in low
    assert "vague" in low


def test_roster_covers_the_four_expected_experts():
    assert {s.name for s in ALL_EXPERTS} == {
        "memory_keeper",
        "strategist",
        "email",
        "researcher",
    }


def test_delegation_tools_warn_against_trivial_use():
    reg = AgentRegistry(get_registry())
    tools = reg.tools_openai()
    assert tools, "no experts available"
    for t in tools:
        desc = t["function"]["description"].lower()
        assert "greetings" in desc or "answer those yourself" in desc


def test_core_experts_are_actually_available():
    # memory + web connectors ship by default, so these three are always wired.
    names = {s.tool_name for s in AgentRegistry(get_registry()).available}
    assert {"ask_memory_keeper", "ask_strategist", "ask_researcher"} <= names


def test_email_expert_is_gated_on_the_gmail_extra():
    # ask_email needs the optional `gmail` extra (google-api-python-client). When
    # it isn't installed the connector can't instantiate, and the expert must be
    # withheld rather than offered as a tool that would fail on use.
    reg = get_registry()
    names = {s.tool_name for s in AgentRegistry(reg).available}
    assert ("ask_email" in names) == ("gmail" in reg.connectors)
