"""Identity — real self-inventory + the first-boot awakening.

  GET  /v1/identity          what Scrappy truthfully knows about himself now
  POST /v1/identity/awaken   birth-or-greet (idempotent); returns his first words

Behind the same bearer guard as the rest of /v1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from apps.api.deps import get_embedder, get_episodic, get_semantic
from core.config import get_settings
from core.identity.bootstrap import awaken
from core.identity.secrets import secrets_overview, set_secret
from core.identity.self_model import gather_inventory
from core.logging import get_logger
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore

log = get_logger(__name__)
router = APIRouter(prefix="/v1/identity", tags=["identity"])


async def _user_id(episodic: EpisodicStore):
    return await episodic.get_or_create_user(get_settings().user_handle)


def _budget(request: Request) -> dict | None:
    """Key-pool status off the app's live LLM client, if present (best-effort)."""
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        return None
    try:
        return llm.keys.status()
    except Exception:
        return None


@router.get("")
async def self_inventory(
    request: Request,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    count = await semantic.count(user_id)
    return await gather_inventory(memory_count=count, budget=_budget(request))


@router.post("/awaken")
async def awaken_endpoint(
    request: Request,
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    user_id = await _user_id(episodic)
    count = await semantic.count(user_id)
    inv = await gather_inventory(memory_count=count, budget=_budget(request))
    result = await awaken(inv, semantic, embedder, user_id)
    return {
        "born": result.born,
        "first_words": result.first_words,
        "born_at": result.born_at,
        "inventory": inv,
    }


class SecretIn(BaseModel):
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)


@router.get("/secrets")
async def list_secrets() -> dict:
    """Which requestable third-party credentials are set (names only, no values)."""
    return {"secrets": secrets_overview()}


@router.post("/secret")
async def put_secret(body: SecretIn) -> dict:
    """Operator-only value entry. The value is written to config and NEVER echoed,
    logged, or returned — the response confirms the name only."""
    try:
        key = set_secret(body.name, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"name": key, "set": True}
