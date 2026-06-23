"""Management console API — backs the web UI at `/`.

Read-mostly endpoints the single-page console calls to render the dashboard:
  GET    /v1/console/identity        live self-context (mission, days, model)
  GET    /v1/console/status          connectors installed + experts available
  GET    /v1/console/memory          browse stored memories (paginated)
  POST   /v1/console/memory          add a memory by hand
  POST   /v1/console/memory/search   semantic search over memories
  DELETE /v1/console/memory/{id}     remove a memory

All of these sit behind the same bearer-token guard as the rest of /v1. The
static UI assets (served at / and /static) are the only things left open so the
page can load and prompt for the key.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.deps import get_embedder, get_episodic, get_semantic
from core.agents.experts import ALL_EXPERTS
from core.config import get_settings
from core.connectors.registry import get_registry
from core.identity.self_model import VERSION, _active_model, days_until
from core.logging import get_logger
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import MemoryKind, SemanticStore

log = get_logger(__name__)
router = APIRouter(prefix="/v1/console", tags=["console"])


# ── identity ─────────────────────────────────────────────────────────


@router.get("/identity")
async def identity() -> dict:
    s = get_settings()
    target = s.mission_target_date
    return {
        "agent": "Scrappy Singh",
        "version": VERSION,
        "operator": "Karnveer Singh",
        "model": _active_model(s),
        "provider": s.llm_provider,
        "mission": s.mission_statement,
        "target_date": target.isoformat(),
        "days_remaining": days_until(target),
        "today": date.today().isoformat(),
    }


# ── system status: connectors + experts ──────────────────────────────


@router.get("/status")
async def status() -> dict:
    reg = get_registry()
    installed = set(reg.connectors.keys())

    connectors = []
    for conn in reg.connectors.values():
        m = conn.manifest
        connectors.append(
            {
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "tools": [
                    {
                        "name": t.name,
                        "executor": t.executor,
                        "requires_approval": t.requires_approval,
                    }
                    for t in m.tools
                ],
            }
        )

    experts = []
    for spec in ALL_EXPERTS:
        missing = [ns for ns in spec.tool_namespaces if ns not in installed]
        experts.append(
            {
                "name": spec.name,
                "title": spec.title,
                "expertise": spec.expertise,
                "tool_namespaces": list(spec.tool_namespaces),
                "available": not missing,
                "missing_connectors": missing,
            }
        )

    return {"connectors": connectors, "experts": experts}


# ── memory browser ────────────────────────────────────────────────────


class AddMemory(BaseModel):
    content: str = Field(min_length=1)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    kind: MemoryKind = "factual"


class SearchMemory(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=10, ge=1, le=50)


async def _user_id(episodic: EpisodicStore) -> UUID:
    return await episodic.get_or_create_user(get_settings().user_handle)


@router.get("/memory")
async def list_memory(
    limit: int = 50,
    offset: int = 0,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    limit = max(1, min(limit, 200))
    rows = await semantic.list_recent(user_id, limit=limit, offset=max(0, offset))
    total = await semantic.count(user_id)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "memories": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "content": r.content,
                "source": r.source,
                "importance": round(r.importance, 2),
                "created_at": r.created_at.isoformat(),
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "use_count": r.use_count,
            }
            for r in rows
        ],
    }


@router.post("/memory", status_code=201)
async def add_memory(
    body: AddMemory,
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    content = body.content.strip()
    try:
        emb = await embedder.embed(content)
    except Exception as e:
        raise HTTPException(503, f"embedder unavailable: {e}") from e
    mem_id = await semantic.write(
        user_id, body.kind, content, emb, source="console", importance=body.importance
    )
    log.info("console.memory_added", id=str(mem_id))
    return {"id": str(mem_id), "status": "saved"}


@router.post("/memory/search")
async def search_memory(
    body: SearchMemory,
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    try:
        emb = await embedder.embed(body.query.strip())
    except Exception as e:
        raise HTTPException(503, f"embedder unavailable: {e}") from e
    rows = await semantic.search(user_id, emb, k=body.k)
    return {
        "query": body.query,
        "results": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "content": r.content,
                "importance": round(r.importance, 2),
                "similarity": round(r.similarity, 3),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.delete("/memory/{memory_id}")
async def delete_memory(
    memory_id: UUID,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    deleted = await semantic.delete(user_id, memory_id)
    if not deleted:
        raise HTTPException(404, "memory not found")
    return {"id": str(memory_id), "status": "deleted"}
