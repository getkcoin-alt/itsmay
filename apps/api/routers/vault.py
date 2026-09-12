"""Vault endpoints — export and import a portable Scrappy.

`POST /v1/vault/export` writes a bundle to a directory on the server; `POST
/v1/vault/export/archive` streams an ephemeral tar to the authenticated caller;
`POST /v1/vault/import/archive` accepts an authenticated archive restore; `POST
/v1/vault/import` merges a server-side directory. All sit behind the bearer-auth
middleware like every other /v1 route: a vault is the whole of Scrappy's
continuity, so moving one is at least as sensitive as reading memory.

The archive endpoints exist specifically so the operator can move live Vault
state off a cloud provider, encrypt it locally, and restore to a clean host. A
transport tar is NOT encryption and is kept only in temporary storage.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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
from core.vault.transport import UnsafeVaultArchive, extract_vault_archive, write_vault_archive

log = get_logger(__name__)
router = APIRouter(prefix="/v1/vault", tags=["vault"])

#: Where bundles land when the caller doesn't name a path.
DEFAULT_EXPORT_DIR = "~/.itsmay/vault"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


class ExportBody(BaseModel):
    out: str = Field(default=DEFAULT_EXPORT_DIR, description="Directory to write into.")
    include_episodes: bool = True


class ExportArchiveBody(BaseModel):
    include_episodes: bool = True


class ImportBody(BaseModel):
    path: str = Field(description="Directory holding a vault bundle.")
    dry_run: bool = False


def _expand(raw: str) -> Path:
    """Resolve a caller-supplied path without touching its contents."""
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


async def _merge_live_bundle(
    bundle: VaultBundle,
    *,
    episodic: EpisodicStore,
    semantic: SemanticStore,
    embedder: Embedder,
    dry_run: bool,
) -> dict:
    user_id = await _user_id(episodic)
    report = await import_bundle(
        bundle,
        semantic=semantic,
        embedder=embedder,
        user_id=user_id,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "source": bundle.summary(),
        **report.to_dict(),
    }


def _copy_limited(source: BinaryIO, target: Path, limit: int) -> int:
    total = 0
    with target.open("wb") as dst:
        while True:
            chunk = source.read(_COPY_CHUNK)
            if not chunk:
                return total
            total += len(chunk)
            if total > limit:
                raise ValueError(f"vault archive exceeds {limit} byte restore limit")
            dst.write(chunk)


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
    """Stream one ephemeral tar of the current Vault to the authenticated caller."""
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
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/import/archive")
async def import_vault_archive(
    archive: UploadFile = File(...),
    dry_run: bool = True,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
    embedder: Embedder = Depends(get_embedder),
) -> dict:
    """Restore an uploaded portable Vault archive, defaulting to a dry run.

    The operator must explicitly set ``dry_run=false`` for mutation. Upload size,
    tar paths, links and Vault protocol compatibility are validated before merge.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="vault-import-"))
    archive_path = tmp_root / "vault.tar"
    try:
        await archive.seek(0)
        try:
            size = await asyncio.to_thread(
                _copy_limited,
                archive.file,
                archive_path,
                MAX_ARCHIVE_BYTES,
            )
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e)) from None

        try:
            bundle_dir = await asyncio.to_thread(
                extract_vault_archive,
                archive_path,
                tmp_root / "unpacked",
            )
            bundle = await asyncio.to_thread(VaultBundle.read, bundle_dir)
        except IncompatibleVault as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        except (MalformedVault, UnsafeVaultArchive) as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

        log.info("vault.import.archive_received", bytes=size, dry_run=dry_run)
        return await _merge_live_bundle(
            bundle,
            episodic=episodic,
            semantic=semantic,
            embedder=embedder,
            dry_run=dry_run,
        )
    finally:
        await archive.close()
        await asyncio.to_thread(shutil.rmtree, tmp_root, True)


@router.post("/import")
async def import_vault(
    body: ImportBody,
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
    embedder: Embedder = Depends(get_embedder),
) -> dict:
    """Merge a server-side bundle into this host, re-embedding memories locally."""
    path = _expand(body.path)
    try:
        bundle = await asyncio.to_thread(VaultBundle.read, path)
    except IncompatibleVault as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except MalformedVault as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return await _merge_live_bundle(
        bundle,
        episodic=episodic,
        semantic=semantic,
        embedder=embedder,
        dry_run=body.dry_run,
    )
