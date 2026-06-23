"""Memory connector — server-side long-term memory tools.

Exposes `memory.save` and `memory.search` over the existing semantic store
(Postgres + pgvector) and embedder. These run *in-process* (executor="server"),
so unlike the Mac tools they execute inside the chat request and their results
are fed back to the model — which is exactly what the Memory Keeper expert needs
to remember a fact and confirm it, or to look something up and report it.

Both tools read their service handles (embedder, semantic store, user uuid) off
the `InvocationContext` the chat router populates per request.
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.logging import get_logger

log = get_logger(__name__)

_MEMORY_TOOLS = [
    ToolSpec(
        name="save",
        description=(
            "Persist a durable fact to long-term memory so it can be recalled in "
            "future conversations. Pass ONE clean, self-contained sentence."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember, as a standalone sentence.",
                },
                "importance": {
                    "type": "number",
                    "description": "0.0-1.0. Higher = more central (identity, goals).",
                },
            },
            "required": ["content"],
        },
        executor="server",
        side_effects=["memory.write"],
    ),
    ToolSpec(
        name="search",
        description=(
            "Search long-term memory by meaning and return the most relevant "
            "stored facts. Use a focused natural-language query."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall."},
                "k": {
                    "type": "integer",
                    "description": "Max results to return (default 5).",
                },
            },
            "required": ["query"],
        },
        executor="server",
    ),
]


class MemoryConnector(Connector):
    manifest = ConnectorManifest(
        name="memory",
        version="0.1.0",
        description="Persist and recall Karnveer's durable facts (semantic memory).",
        tools=_MEMORY_TOOLS,
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if ctx.embedder is None or ctx.semantic is None or ctx.user_uuid is None:
            raise RuntimeError(
                "memory connector needs embedder, semantic store, and user_uuid in ctx"
            )

        if action == "save":
            content = str(args.get("content", "")).strip()
            if not content:
                return "nothing to save (empty content)"
            importance = _clamp(args.get("importance", 0.6))
            emb = await ctx.embedder.embed(content)
            mem_id = await ctx.semantic.write(
                ctx.user_uuid,
                "factual",
                content,
                emb,
                source="agent:memory_keeper",
                importance=importance,
            )
            log.info("memory.saved", id=str(mem_id), importance=importance)
            return f"saved (importance {importance:.2f})"

        if action == "search":
            query = str(args.get("query", "")).strip()
            if not query:
                return "no query given"
            k = int(args.get("k", 5) or 5)
            emb = await ctx.embedder.embed(query)
            rows = await ctx.semantic.search(ctx.user_uuid, emb, k=max(1, min(k, 20)))
            if not rows:
                return "no matching memories found"
            return [
                {
                    "content": r.content,
                    "importance": round(r.importance, 2),
                    "similarity": round(r.similarity, 3),
                }
                for r in rows
            ]

        return f"unknown memory action: {action}"


def _clamp(v: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.6
    return max(lo, min(hi, f))
