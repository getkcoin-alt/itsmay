"""POST /v1/memory/consolidate — trigger nightly memory consolidation on demand."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.api.deps import get_embedder, get_episodic, get_llm, get_semantic
from core.brain.llm import LLMClient
from core.config import get_settings
from core.memory.consolidator import consolidate_today
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore

router = APIRouter(prefix="/v1/memory", tags=["memory"])


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
