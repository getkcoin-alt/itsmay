"""Vault endpoints — export and import a portable Scrappy.

`POST /v1/vault/export` writes a bundle to a directory on the server; `POST
/v1/vault/export/archive` streams an ephemeral tar to the authenticated caller;
`POST /v1/vault/import` merges one back in. All sit behind the bearer-auth
middleware like every other /v1 route: a vault is the whole of Scrappy's
continuity, so reading one is at least as sensitive as reading memory.

The archive endpoint exists specifically so the operator can move live Vault
state off a cloud provider and encrypt it locally. The tar itself is NOT
encrypted and is deleted from server temp storage after the response completes.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

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
from core.vault.transport import write_vault_archive

log = get_logger(__name__)
router = APIRouter(prefix="/v1/vault", tags=["vault"])

#: Where bundles land when the caller doesn't name a path.
DEFAULT_EXPORT_DIR = "~/.itsmay/vault"


class ExportBody(BaseModel):
    out: str = Field(default=DEFAULT_EXPORT_DIR, description="Directory to write into.")
    include_episodes: bool = True


class ExportArchiveBody(BaseModel):
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


async def _build_live_bundle(
    *,
    episodic: EpisodicStore,
    semantic: SemanticStore,
    include_episodes: bool,
) -> VaultBundle:
    user_id = await _user_id(episodic)
    try:
        return await build_bundle(
            semantic=semantic,
            episodic=episodic,
            user_id=user_id,
            include_episodes=include_episodes,
        )
    except SecretInVault as e:
        log.warning("vault.export.blocked", kind=e.kind, where=e.where)
        raise HTTPException(status_code=409, detail=str(e)) from None


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
    bundle = await _build_live_bundle(
        episodic=episodic,
        semantic=semantic,
        include_episodes=body.include_episodes,
    )

    out = _expand(body.out)
    try:
        # Writing a bundle is real blocking file I/O — keep it off the event loop
        # so a large vault can't stall every other in-flight request.
        await asyncio.to_thread(bundle.write, out)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"cannot write to {out}: {e}") from None
    return {"ok": True, "path": str(out), **bundle.summary()}


@router.post("/export/archive", response_class=FileResponse)
async def export_vault_archive(
    body: ExportArchiveBody,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> FileResponse:
    """Stream one ephemeral tar of the current Vault to the authenticated caller.

    The archive is plaintext transport, not a backup destination. A caller that
    includes episodes should encrypt it locally immediately and remove the tar.
    Server-side temporary files are removed by a response background task.
    """
    bundle = await _build_live_bundle(
        episodic=episodic,
        semantic=semantic,
        include_episodes=body.include_episodes,
    )

    tmp_root = Path(tempfile.mkdtemp(prefix="vault-export-"))
    archive = tmp_root / "vault.tar"
    try:
        await asyncio.to_thread(write_vault_archive, bundle, archive, tmp_root / "work")
    except OSError as e:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"cannot build vault archive: {e}") from None
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise

    return FileResponse(
        path=archive,
        media_type="application/x-tar",
        filename="scrappy-vault.tar",
        background=BackgroundTask(shutil.rmtree, tmp_root, ignore_errors=True),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
