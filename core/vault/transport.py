"""Safe transport helpers for moving a VaultBundle between hosts.

The canonical Vault protocol is a directory. Transport wraps that directory in a
plain tar archive so an authenticated API can stream one file and a local client
can unpack it before applying the existing VaultBundle reader/import rules.

This archive is NOT encryption. A tar that includes episodes is private data and
must be handled ephemerally, then wrapped by the encrypted continuity capsule on
the operator's machine.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from core.vault.bundle import VaultBundle

_ARCHIVE_ROOT = "vault"


class UnsafeVaultArchive(ValueError):
    """Archive attempted path traversal, links, devices, or an invalid layout."""


def write_vault_archive(bundle: VaultBundle, archive_path: Path, work_dir: Path) -> Path:
    """Write ``bundle`` as a deterministic-layout tar archive.

    ``work_dir`` is caller-owned temporary storage. The function deliberately
    does not invent a persistent server path for private data.
    """
    archive_path = Path(archive_path)
    work_dir = Path(work_dir)
    bundle_dir = work_dir / _ARCHIVE_ROOT
    bundle.write(bundle_dir)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "w") as tar:
        for path in sorted(bundle_dir.rglob("*")):
            if not path.is_file():
                continue
            arcname = Path(_ARCHIVE_ROOT) / path.relative_to(bundle_dir)
            info = tar.gettarinfo(str(path), arcname=str(arcname))
            # Stable metadata avoids leaking host username/group and makes the
            # wrapper reproducible apart from the bundle's own timestamps.
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as fh:
                tar.addfile(info, fh)
    return archive_path


def extract_vault_archive(archive_path: Path, destination: Path) -> Path:
    """Safely extract a transported Vault bundle and return its directory."""
    archive_path = Path(archive_path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    with tarfile.open(archive_path, "r") as tar:
        members = tar.getmembers()
        for member in members:
            if member.issym() or member.islnk() or member.isdev():
                raise UnsafeVaultArchive("vault archive contains links or device entries")
            candidate = (root / member.name).resolve()
            if candidate != root and root not in candidate.parents:
                raise UnsafeVaultArchive("vault archive contains path traversal")
            parts = Path(member.name).parts
            if not parts or parts[0] != _ARCHIVE_ROOT:
                raise UnsafeVaultArchive("vault archive has an unexpected top-level layout")
        tar.extractall(destination, members=members, filter="data")

    bundle_dir = destination / _ARCHIVE_ROOT
    # Parse immediately so malformed transports fail before callers trust paths.
    VaultBundle.read(bundle_dir)
    return bundle_dir
