"""Vault endpoints — export and import a portable Scrappy.

`POST /v1/vault/export` writes a bundle to a directory on the server; `POST
/v1/vault/import` merges one back in. Both sit behind the bearer-auth
middleware like every other /v1 route: a vault is the whole of Scrappy's
continuity, so reading one is at least as sensitive as reading memory.

The endpoints are thin — schema, redaction and merge rules all live in
`core/vault/`, so the CLI and any future consumer share exactly one
implementation of the protocol.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.deps import get_embedder, get_episodic, get_semantic
from core.config import get_settings
from core.logging import get_logger
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore
from core.vault.bundle import MalformedVault, VaultBundle
from core.vault.export import build_bundle
from core.vault.import_ import import_bundle
from core.vault.redact import SecretInVault
from core.vault.schema import IncompatibleVault

log = get_logger(__name__)
router = APIRouter(prefix="/v1/vault", tags=["vault"])

#: Where bundles land when the caller doesn't name a path.
DEFAULT_EXPORT_DIR = "~/.itsmay/vault"


class ExportBody(BaseModel):
    out: str = Field(default=DEFAULT_EXPORT_DIR, description="Directory to write into.")
    include_episodes: bool = True


class ImportBody(BaseModel):
    path: str = Field(description="Directory holding a vault bundle.")
    dry_run: bool = False


def _expand(raw: str) -> Path:
    """Resolve a caller-supplied path. Sync on purpose — pure string work, and it
    keeps the async handlers free of filesystem calls."""
    return Path(raw).expanduser()


async def _user_id(episodic: EpisodicStore) -> UUID:
    return await episodic.get_or_create_user(get_settings().user_handle)


@router.post("/export")
async def export_vault(
    body: ExportBody,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> dict:
    """Write this Scrappy out as a portable bundle.

    Fails with 409 if any record carries something credential-shaped — a vault is
    meant to travel, so a secret inside one is a secret published.
    """
    user_id = await _user_id(episodic)
    try:
        bundle = await build_bundle(
            semantic=semantic,
            episodic=episodic,
            user_id=user_id,
            include_episodes=body.include_episodes,
        )
    except SecretInVault as e:
        log.warning("vault.export.blocked", kind=e.kind, where=e.where)
        raise HTTPException(status_code=409, detail=str(e)) from None

    out = _expand(body.out)
    try:
        # Writing a bundle is real blocking file I/O — keep it off the event loop
        # so a large vault can't stall every other in-flight request.
        await asyncio.to_thread(bundle.write, out)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"cannot write to {out}: {e}") from None
    return {"ok": True, "path": str(out), **bundle.summary()}


@router.post("/import")
async def import_vault(
    body: ImportBody,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
    embedder: Embedder = Depends(get_embedder),
) -> dict:
    """Merge a bundle into this host, re-embedding every memory locally."""
    path = _expand(body.path)
    try:
        bundle = await asyncio.to_thread(VaultBundle.read, path)
    except IncompatibleVault as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except MalformedVault as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    user_id = await _user_id(episodic)
    report = await import_bundle(
        bundle,
        semantic=semantic,
        embedder=embedder,
        user_id=user_id,
        dry_run=body.dry_run,
    )
    return {
        "ok": True,
        "dry_run": body.dry_run,
        "source": bundle.summary(),
        **report.to_dict(),
    }
