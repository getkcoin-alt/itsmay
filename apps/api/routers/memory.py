"""Memory endpoints — consolidation and one-shot RAG seeding."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from apps.api.deps import get_embedder, get_episodic, get_llm, get_semantic
from core.brain.llm import LLMClient
from core.config import get_settings
from core.logging import get_logger
from core.memory.consolidator import consolidate_today
from core.memory.db import get_pool
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore

log = get_logger(__name__)
router = APIRouter(prefix="/v1/memory", tags=["memory"])

# scripts/knowledge.yaml lives at the repo root, four parents up from this file.
KNOWLEDGE_FILE = Path(__file__).resolve().parents[3] / "scripts" / "knowledge.yaml"
SEED_SOURCE = "seed:knowledge.yaml"


@router.post("/consolidate")
async def consolidate(
    llm: LLMClient = Depends(get_llm),
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)
    return await consolidate_today(llm, semantic, embedder, user_id)


@router.post("/seed")
async def seed_memory(
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    """Seed long-term memory from scripts/knowledge.yaml (idempotent).

    Lets RAG be populated on the live deploy with a single authenticated call
    instead of needing shell access to the Railway container.
    """
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)
    if not KNOWLEDGE_FILE.exists():
        return {"error": f"knowledge file not found at {KNOWLEDGE_FILE}", "inserted": 0}

    entries = yaml.safe_load(KNOWLEDGE_FILE.read_text()) or []
    pool = await get_pool()
    inserted = 0
    skipped = 0
    for entry in entries:
        content = str(entry.get("content", "")).strip()
        if not content:
            continue
        importance = float(entry.get("importance", 0.6))
        kind = entry.get("kind", "factual")
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM memories WHERE user_id = $1 AND content = $2 LIMIT 1",
                user_id,
                content,
            )
        if exists:
            skipped += 1
            continue
        embedding = await embedder.embed(content)
        await semantic.write(
            user_id, kind, content, embedding, source=SEED_SOURCE, importance=importance
        )
        inserted += 1

    log.info("memory.seeded", inserted=inserted, skipped=skipped)
    return {"inserted": inserted, "skipped": skipped, "total": len(entries)}
