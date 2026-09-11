from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.vault.bundle import VaultBundle
from core.vault.continuity import (
    CapsuleIntegrityError,
    CapsuleSpec,
    ContinuityRecord,
    PrivateCapsuleRequiresEncryption,
    load_bootstrap_context,
    verify_capsule,
    write_private_staging_capsule,
    write_public_capsule,
)
from core.vault.continuity_crypto import (
    InvalidEncryptedCapsule,
    decrypt_private_capsule,
    inspect_encrypted_header,
    write_encrypted_private_capsule,
)
from core.vault.conversation_ingest import ConversationIngestReport, ConversationTurn
from core.vault.schema import (
    Directive,
    Episode,
    Identity,
    Manifest,
    MemoryRecord,
    Mission,
    Operator,
    VaultState,
    content_hash,
)


def _bundle() -> VaultBundle:
    learned = datetime(2026, 9, 11, 3, 0, tzinfo=UTC)
    memory_text = "Scrappy continuity must survive model-provider changes."
    return VaultBundle(
        manifest=Manifest(
            vault_id="zeta-test",
            exported_by="test-node",
            includes_episodes=True,
        ),
        identity=Identity(
            name="Scrappy Singh",
            operator=Operator(handle="karnveer", name="Karnveer Singh", address_as="BOYI"),
            mission=Mission(statement="Build verifiable continuity."),
            persona=["Sharp and direct."],
            invariants=["Never claim success without proof."],
        ),
        directives=[
            Directive(
                id="d001",
                content="Never fake the magic.",
                content_sha256=content_hash("Never fake the magic."),
            )
        ],
        memories=[
            MemoryRecord(
                id="m1",
                kind="semantic",
                content=memory_text,
                content_sha256=content_hash(memory_text),
                source="operator.seed",
                learned_at=learned,
                learned_by="test-node",
                trust="operator",
            )
        ],
        episodes=[
            Episode(
                id="e1",
                session_id="s1",
                role="user",
                content="BOYI, remember the continuity rule.",
                created_at=learned,
                channel="chat",
            )
        ],
        state=VaultState(memory_count=1, episode_count=1, known_hosts=["test-node"]),
    )


def _spec() -> CapsuleSpec:
    return CapsuleSpec(
        constitution=(
            "# Constitution\n\n"
            "Never fake the magic. Build the mechanism until reality feels magical."
        ),
        project_state={"continuous_presence": {"status": "production"}},
        vocabulary={"🧬": "identity", "🕳️": "continuity", "⚙️": "execution"},
        unresolved_threads=[
            {
                "id": "continuity-private-backup",
                "status": "open",
                "summary": "Complete provider-independent recovery drill.",
            }
        ],
        decisions=[
            ContinuityRecord(
                id="decision-1",
                kind="DECISION",
                content="The provider may host a model; it must never own continuity.",
                source_ref="issue:30",
                trust="operator",
            )
        ],
    )


def test_public_capsule_is_verifiable_and_omits_raw_history(tmp_path: Path) -> None:
    target = write_public_capsule(_bundle(), tmp_path / "capsule", spec=_spec())

    report = verify_capsule(target)
    assert report.ok is True
    assert report.missing_files == []
    assert report.mismatches == []

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["privacy"] == "public-safe"
    assert manifest["includes_raw_episodes"] is False
    assert manifest["counts"]["episodic"] == 0
    assert (target / "memory" / "episodic.jsonl").read_text(encoding="utf-8") == ""

    bootstrap = load_bootstrap_context(target)
    assert "Scrappy Singh" in bootstrap
    assert "Never fake the magic." in bootstrap
    assert "continuous_presence" in bootstrap
    assert "🕳️" in bootstrap


def test_tamper_is_detected_before_bootstrap(tmp_path: Path) -> None:
    target = write_public_capsule(_bundle(), tmp_path / "capsule", spec=_spec())
    with (target / "constitution.md").open("a", encoding="utf-8") as fh:
        fh.write("\nmodified")

    report = verify_capsule(target)
    assert report.ok is False
    assert "sha256 mismatch: constitution.md" in report.mismatches

    with pytest.raises(CapsuleIntegrityError):
        load_bootstrap_context(target)


def test_plaintext_private_history_is_refused_by_default(tmp_path: Path) -> None:
    with pytest.raises(PrivateCapsuleRequiresEncryption):
        write_private_staging_capsule(
            _bundle(),
            tmp_path / "private",
            spec=_spec(),
        )


def test_encrypted_private_capsule_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    encrypted = write_encrypted_private_capsule(
        _bundle(),
        tmp_path / "scrappy-continuity.enc",
        spec=_spec(),
        passphrase="correct horse battery staple",
    )

    assert encrypted.is_file()
    assert not (tmp_path / "capsule").exists()
    header = inspect_encrypted_header(encrypted)
    assert header["format"] == "scrappy-continuity-enc-v1"
    assert header["cipher"] == "AES-256-GCM"

    restored_root = decrypt_private_capsule(
        encrypted,
        tmp_path / "restored",
        passphrase="correct horse battery staple",
    )
    restored = restored_root / "scrappy-continuity"
    report = verify_capsule(restored)
    assert report.ok is True

    manifest = json.loads((restored / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["privacy"] == "encrypted-private"
    assert manifest["includes_raw_episodes"] is True
    history = (restored / "memory" / "episodic.jsonl").read_text(encoding="utf-8")
    assert "remember the continuity rule" in history


def test_encrypted_capsule_carries_normalized_external_conversation(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    report = ConversationIngestReport(
        source_path="/private/conversations.json",
        conversations=1,
        turns=[
            ConversationTurn(
                id="conv:turn",
                conversation_id="conv",
                title="North Star",
                role="user",
                content="🧬🕳️⚙️",
                source_sha256="a" * 64,
            )
        ],
    )
    encrypted = write_encrypted_private_capsule(
        _bundle(),
        tmp_path / "scrappy-with-export.enc",
        spec=_spec(),
        passphrase="correct horse battery staple",
        conversation_report=report,
    )

    restored_root = decrypt_private_capsule(
        encrypted,
        tmp_path / "restored-export",
        passphrase="correct horse battery staple",
    )
    restored = restored_root / "scrappy-continuity"
    integrity = verify_capsule(restored)
    assert integrity.ok is True

    manifest = json.loads((restored / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["external_conversation_turns"] == 1
    turns = (restored / "conversation" / "turns.jsonl").read_text(encoding="utf-8")
    assert "🧬🕳️⚙️" in turns
    provenance = (restored / "provenance" / "conversation_sources.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"source_sha256": "aaaaaaaa' in provenance


def test_wrong_passphrase_never_extracts_private_capsule(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    encrypted = write_encrypted_private_capsule(
        _bundle(),
        tmp_path / "scrappy-continuity.enc",
        spec=_spec(),
        passphrase="correct horse battery staple",
    )

    with pytest.raises(InvalidEncryptedCapsule):
        decrypt_private_capsule(
            encrypted,
            tmp_path / "wrong",
            passphrase="definitely the wrong password",
        )
