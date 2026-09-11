from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.vault.bundle import VaultBundle
from core.vault.continuity import CapsuleSpec, verify_capsule
from core.vault.continuity_crypto import decrypt_private_capsule, write_encrypted_private_capsule
from core.vault.schema import (
    Identity,
    Manifest,
    MemoryRecord,
    Mission,
    Operator,
    VaultState,
    content_hash,
)


def _bundle() -> VaultBundle:
    text = "A private continuity capsule must contain a deterministic restore source."
    return VaultBundle(
        manifest=Manifest(vault_id="restore-source", exported_by="node-test"),
        identity=Identity(
            name="Scrappy Singh",
            operator=Operator(handle="karnveer"),
            mission=Mission(statement="Restore verifiably."),
        ),
        memories=[
            MemoryRecord(
                id="m-restore",
                kind="semantic",
                content=text,
                content_sha256=content_hash(text),
                learned_at=datetime(2026, 9, 11, 3, 0, tzinfo=UTC),
            )
        ],
        state=VaultState(memory_count=1),
    )


def test_encrypted_capsule_embeds_importable_vault_bundle(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    encrypted = write_encrypted_private_capsule(
        _bundle(),
        tmp_path / "continuity.enc",
        spec=CapsuleSpec(constitution="# Constitution\n\nNever fabricate continuity."),
        passphrase="correct horse battery staple",
    )

    root = decrypt_private_capsule(
        encrypted,
        tmp_path / "decrypted",
        passphrase="correct horse battery staple",
    )
    capsule = root / "scrappy-continuity"
    assert verify_capsule(capsule).ok is True

    embedded = VaultBundle.read(capsule / "vault_bundle")
    assert embedded.manifest.vault_id == "restore-source"
    assert embedded.identity.name == "Scrappy Singh"
    assert [memory.id for memory in embedded.memories] == ["m-restore"]
