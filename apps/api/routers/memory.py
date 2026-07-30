"""Memory endpoints — consolidation and one-shot RAG seeding."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.deps import get_embedder, get_episodic, get_llm, get_semantic
from core.brain.llm import LLMClient
from core.config import get_settings
from core.logging import get_logger
from core.memory.consolidator import consolidate_today
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.procedural import mine_workflows
from core.memory.semantic import SemanticStore

log = get_logger(__name__)
router = APIRouter(prefix="/v1/memory", tags=["memory"])

# scripts/knowledge.yaml lives at the repo root, four parents up from this file.
KNOWLEDGE_FILE = Path(__file__).resolve().parents[3] / "scripts" / "knowledge.yaml"
SEED_SOURCE = "seed:knowledge.yaml"


@router.post("/reindex")
async def reindex() -> dict:
    """Rebuild the pgvector indexes, sized for the data that's actually stored.

    Worth running once real memory has accumulated: an ivfflat index trains its
    clusters from the rows present when it was built, so one created on an empty
    table never learns the shape of the data. Postgres-only — SQLite scores
    vectors directly and has nothing to rebuild.
    """
    from core.memory.backend import resolve_backend

    if resolve_backend() != "postgres":
        return {
            "ok": True,
            "skipped": True,
            "backend": "sqlite",
            "note": (
                "SQLite scores every vector directly — there's no ANN index to "
                "rebuild, and recall is already exact."
            ),
        }

    from core.memory.db import get_pool
    from core.memory.maintenance import reindex_vector_indexes

    result = await reindex_vector_indexes(await get_pool())
    log.info("memory.reindex", ok=result.get("ok"))
    return {"backend": "postgres", "skipped": False, **result}


@router.get("/stats")
async def memory_stats(
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    """Count of long-term memories stored for the configured user (RAG size)."""
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)
    count = await semantic.count(user_id)
    return {"count": int(count or 0)}


@router.post("/consolidate")
async def consolidate(
    llm: LLMClient = Depends(get_llm),
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)
    # Two passes over today's activity: durable facts (from the conversation) and
    # reusable workflows (from the tool traces). Both write long-term memories.
    facts = await consolidate_today(llm, semantic, embedder, user_id)
    workflows = await mine_workflows(llm, semantic, embedder, episodic, user_id)
    return {"facts": facts, "workflows": workflows}


class SeedBody(BaseModel):
    # The client (scrappy seed) sends the parsed knowledge entries, so seeding
    # never depends on scripts/ being present in the deployed image. Each entry:
    # {content, importance?, kind?}.
    entries: list[dict] | None = None


@router.post("/seed")
async def seed_memory(
    body: SeedBody | None = None,
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    """Seed long-term memory (idempotent).

    Entries come from the request body when provided (the CLI ships them from the
    repo's scripts/knowledge.yaml); otherwise we fall back to a server-side copy
    of that file if one happens to be present.
    """
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)

    if body is not None and body.entries:
        entries = body.entries
    elif KNOWLEDGE_FILE.exists():
        entries = yaml.safe_load(KNOWLEDGE_FILE.read_text()) or []
    else:
        return {
            "error": (
                "no entries provided and no server-side knowledge.yaml found — "
                "run `scrappy seed` from the repo so it can ship the entries"
            ),
            "inserted": 0,
        }

    inserted = 0
    skipped = 0
    for entry in entries:
        content = str(entry.get("content", "")).strip()
        if not content:
            continue
        importance = float(entry.get("importance", 0.6))
        kind = entry.get("kind", "factual")
        if await semantic.content_exists(user_id, content):
            skipped += 1
            continue
        embedding = await embedder.embed(content)
        await semantic.write(
            user_id, kind, content, embedding, source=SEED_SOURCE, importance=importance
        )
        inserted += 1

    log.info("memory.seeded", inserted=inserted, skipped=skipped)
    return {"inserted": inserted, "skipped": skipped, "total": len(entries)}
