"""Vector-index maintenance for the Postgres backend.

An ivfflat index groups vectors into `lists` clusters at BUILD time and a query
scans `probes` of them. Both numbers have to suit how much data you actually
have, and the schema had to pick them before a single memory existed:

- `lists` too high for the row count leaves a handful of vectors per cluster, so
  the default `probes = 1` looks at almost nothing and recall collapses.
- The clusters are trained from whatever rows existed when the index was built.
  An index created on an empty table never learns the real shape of the data.

So this is genuinely maintenance, not setup: it should be re-run once there's a
meaningful amount of memory. `scrappy reindex` calls it.

The sizing functions are pure and unit-tested; only `reindex_vector_indexes`
touches the database.
"""

from __future__ import annotations

import math

from core.logging import get_logger

log = get_logger(__name__)

# Vector indexes we manage: (index name, table, column).
VECTOR_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_memories_embedding", "memories", "embedding"),
    ("idx_messages_embedding", "messages", "embedding"),
)


def optimal_lists(rows: int) -> int:
    """pgvector's sizing rule: rows/1000 up to 1M rows, then sqrt(rows).

    Small datasets land on 1 list, which means a scan of everything — exactly
    right when there's little data: perfect recall at negligible cost.
    """
    if rows <= 0:
        return 1
    if rows < 1_000_000:
        return max(1, rows // 1000)
    return max(1, int(math.sqrt(rows)))


def optimal_probes(lists: int) -> int:
    """How many clusters a query should scan: sqrt(lists), never more than lists.

    This is the recall dial. Leaving it at the default 1 against a many-list
    index means most of the data is invisible to a search.
    """
    if lists <= 1:
        return 1
    return max(1, min(lists, int(math.sqrt(lists))))


async def reindex_vector_indexes(pool) -> dict:
    """Rebuild each vector index sized for the data that's actually there now.

    Returns a per-index report plus the `probes` value the new sizing wants.
    Takes a brief exclusive lock per index while it rebuilds — fine at personal
    scale, and the reason this is an explicit command rather than automatic.
    """
    report: list[dict] = []
    max_lists = 1
    async with pool.acquire() as conn:
        for index_name, table, column in VECTOR_INDEXES:
            rows = await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
            )
            rows = int(rows or 0)
            lists = optimal_lists(rows)
            max_lists = max(max_lists, lists)
            # `lists` is an int we computed — never user input — so interpolating
            # it into the DDL (which cannot take a bind parameter) is safe.
            try:
                async with conn.transaction():
                    await conn.execute(f"DROP INDEX IF EXISTS {index_name}")
                    await conn.execute(
                        f"CREATE INDEX {index_name} ON {table} "
                        f"USING ivfflat ({column} vector_cosine_ops) "
                        f"WITH (lists = {lists})"
                    )
                await conn.execute(f"ANALYZE {table}")
                report.append(
                    {"index": index_name, "rows": rows, "lists": lists, "ok": True}
                )
                log.info("memory.reindexed", index=index_name, rows=rows, lists=lists)
            except Exception as e:
                log.warning("memory.reindex_failed", index=index_name, err=str(e))
                report.append(
                    {"index": index_name, "rows": rows, "lists": lists,
                     "ok": False, "error": str(e)}
                )

    probes = optimal_probes(max_lists)
    return {
        "ok": all(r["ok"] for r in report),
        "indexes": report,
        "recommended_probes": probes,
        "note": (
            f"Set IVFFLAT_PROBES={probes} so searches scan enough clusters "
            "(the default of 1 would miss most of the data)."
        ),
    }
