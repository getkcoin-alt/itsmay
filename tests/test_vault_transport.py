from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.vault.bundle import VaultBundle
from core.vault.schema import (
    Identity,
    Manifest,
    MemoryRecord,
    Mission,
    Operator,
    VaultState,
    content_hash,
)
from core.vault.transport import UnsafeVaultArchive, extract_vault_archive, write_vault_archive


def _bundle() -> VaultBundle:
    text = "Continuity must remain portable."
    return VaultBundle(
        manifest=Manifest(vault_id="transport-test", exported_by="node-a"),
        identity=Identity(
            name="Scrappy Singh",
            operator=Operator(handle="karnveer"),
            mission=Mission(statement="Preserve continuity."),
        ),
        memories=[
            MemoryRecord(
                id="m1",
                kind="semantic",
                content=text,
                content_sha256=content_hash(text),
                learned_at=datetime(2026, 9, 11, 3, 0, tzinfo=UTC),
            )
        ],
        state=VaultState(memory_count=1),
    )


def test_archive_round_trip(tmp_path: Path) -> None:
    archive = write_vault_archive(_bundle(), tmp_path / "vault.tar", tmp_path / "work")
    bundle_dir = extract_vault_archive(archive, tmp_path / "restore")
    restored = VaultBundle.read(bundle_dir)

    assert restored.manifest.vault_id == "transport-test"
    assert restored.identity.name == "Scrappy Singh"
    assert [m.content for m in restored.memories] == ["Continuity must remain portable."]


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar"
    payload = b"owned"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name="../outside.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(UnsafeVaultArchive):
        extract_vault_archive(archive, tmp_path / "restore")
    assert not (tmp_path / "outside.txt").exists()


def test_archive_rejects_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name="vault/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    with pytest.raises(UnsafeVaultArchive):
        extract_vault_archive(archive, tmp_path / "restore")
