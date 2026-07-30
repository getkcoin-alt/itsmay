"""Vector-index maintenance — sizing math + the reindex endpoint.

The sizing rules are pure, so they're tested directly. The DB rebuild itself is
exercised against a fake pool (no Postgres in CI), and the endpoint's SQLite path
is checked end-to-end since that's the local default.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.memory.maintenance import (
    VECTOR_INDEXES,
    optimal_lists,
    optimal_probes,
    reindex_vector_indexes,
)

# ── sizing math ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rows,expected",
    [
        (0, 1),  # empty table → one list (a full scan, which is exact)
        (500, 1),  # small store → still one list; ANN would only hurt recall
        (1_000, 1),
        (50_000, 50),  # pgvector's rows/1000 rule
        (999_999, 999),
    ],
)
def test_optimal_lists_follows_pgvector_rule(rows, expected):
    assert optimal_lists(rows) == expected


def test_optimal_lists_switches_to_sqrt_past_a_million():
    assert optimal_lists(4_000_000) == 2000  # sqrt, not rows/1000
    assert optimal_lists(-5) == 1  # nonsense input never yields an invalid index


@pytest.mark.parametrize("lists,expected", [(1, 1), (4, 2), (100, 10), (2500, 50)])
def test_optimal_probes_is_sqrt_of_lists(lists, expected):
    assert optimal_probes(lists) == expected


def test_probes_never_exceeds_lists():
    for lists in range(1, 40):
        assert 1 <= optimal_probes(lists) <= lists


# ── rebuild against a fake pool ───────────────────────────────────────


class _FakeConn:
    def __init__(self, counts: dict[str, int], fail_on: str | None = None) -> None:
        self.counts = counts
        self.fail_on = fail_on
        self.executed: list[str] = []

    async def fetchval(self, sql: str):
        table = "memories" if "memories" in sql else "messages"
        return self.counts.get(table, 0)

    async def execute(self, sql: str):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("permission denied")
        self.executed.append(" ".join(sql.split()))

    def transaction(self):
        return _Noop()


class _Noop:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self):
        pool_conn = self.conn

        class _Acq:
            async def __aenter__(self):
                return pool_conn

            async def __aexit__(self, *a):
                return False

        return _Acq()


async def test_reindex_sizes_each_index_to_its_own_row_count():
    conn = _FakeConn({"memories": 50_000, "messages": 0})
    result = await reindex_vector_indexes(_FakePool(conn))

    assert result["ok"] is True
    by_index = {r["index"]: r for r in result["indexes"]}
    assert by_index["idx_memories_embedding"]["lists"] == 50  # 50k/1000
    assert by_index["idx_messages_embedding"]["lists"] == 1  # empty
    # Probes follow the LARGEST index, so a search there still has recall.
    assert result["recommended_probes"] == optimal_probes(50)


async def test_reindex_drops_recreates_and_analyzes():
    conn = _FakeConn({"memories": 2_000, "messages": 2_000})
    await reindex_vector_indexes(_FakePool(conn))
    sql = " | ".join(conn.executed)
    for index_name, table, _ in VECTOR_INDEXES:
        assert f"DROP INDEX IF EXISTS {index_name}" in sql
        assert f"CREATE INDEX {index_name} ON {table}" in sql
        assert f"ANALYZE {table}" in sql
    assert "vector_cosine_ops" in sql
    assert "lists = 2" in sql


async def test_reindex_reports_failure_without_raising():
    # A locked or permission-denied rebuild must be reported, not crash the call.
    conn = _FakeConn({"memories": 10}, fail_on="CREATE INDEX idx_memories_embedding")
    result = await reindex_vector_indexes(_FakePool(conn))
    assert result["ok"] is False
    failed = next(r for r in result["indexes"] if r["index"] == "idx_memories_embedding")
    assert failed["ok"] is False and "permission denied" in failed["error"]
    # The other index still got rebuilt.
    assert any(r["ok"] for r in result["indexes"])


# ── endpoint ──────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from apps.api.main import app

    return TestClient(app)


def test_reindex_endpoint_is_a_no_op_on_sqlite(client):
    # SQLite scores vectors directly — there's no ANN index, and recall is exact.
    r = client.post("/v1/memory/reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["skipped"] is True
    assert body["backend"] == "sqlite"
    assert "note" in body
