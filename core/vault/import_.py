"""Import a vault bundle into this host's stores.

This is where portability is actually paid for: the bundle carries no vectors, so
every incoming memory is **re-embedded here** with whatever embedder this host
runs. That is what lets a vault written by a 384-dim fastembed host be read by
one running a different model entirely.

Merge rules (also in `docs/VAULT_PROTOCOL.md`):

* Memories are **deduped by `content_sha256`**, not by id — the same fact
  re-exported from two hosts is one fact, and ids won't match across stores.
* Identity and directives resolve by **highest `revision`**; equal revisions keep
  what is already here (an import must not silently rewrite who you are).
* Nothing is deleted. An import can only add — reconciling a deletion across
  hosts needs tombstones, which v1 does not have, and pretending otherwise would
  lose data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from core.logging import get_logger
from core.vault.bundle import VaultBundle
from core.vault.schema import Identity, MemoryRecord, content_hash

log = get_logger(__name__)


@dataclass(slots=True)
class ImportReport:
    """What actually happened — reported, never assumed."""

    memories_added: int = 0
    memories_skipped: int = 0  # already present (same content hash)
    memories_failed: int = 0  # embedding or write failed
    identity_applied: bool = False
    identity_reason: str = ""
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memories_added": self.memories_added,
            "memories_skipped": self.memories_skipped,
            "memories_failed": self.memories_failed,
            "identity_applied": self.identity_applied,
            "identity_reason": self.identity_reason,
            "conflicts": self.conflicts,
        }

    def summary(self) -> str:
        bits = [f"{self.memories_added} added"]
        if self.memories_skipped:
            bits.append(f"{self.memories_skipped} already known")
        if self.memories_failed:
            bits.append(f"{self.memories_failed} failed")
        identity = "identity applied" if self.identity_applied else "identity unchanged"
        return f"{', '.join(bits)}; {identity}."


def resolve_identity(
    incoming: Identity, current: Identity | None
) -> tuple[Identity, bool, str]:
    """Pick which identity wins. Returns (identity, changed, reason).

    Higher revision wins. A tie keeps what's already here — importing an equal
    revision should be a no-op, not a coin flip, or two hosts would ping-pong
    Scrappy's persona between them forever.
    """
    if current is None:
        return incoming, True, "no local identity — adopted the bundle's"
    if incoming.revision > current.revision:
        return (
            incoming,
            True,
            f"bundle revision {incoming.revision} > local {current.revision}",
        )
    if incoming.revision < current.revision:
        return (
            current,
            False,
            f"kept local revision {current.revision} (bundle had {incoming.revision})",
        )
    return current, False, f"same revision {current.revision} — kept local"


async def import_bundle(
    bundle: VaultBundle,
    *,
    semantic: Any,
    embedder: Any,
    user_id: UUID,
    existing_hashes: set[str] | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Merge `bundle` into this host's semantic store.

    `existing_hashes` lets a caller pre-compute what's already known; when it's
    None we build it from the store. `dry_run` reports what would change without
    writing anything.
    """
    report = ImportReport()

    if existing_hashes is None:
        existing_hashes = await _known_hashes(semantic, user_id)

    for record in bundle.memories:
        digest = record.content_sha256 or content_hash(record.content)
        if digest in existing_hashes:
            report.memories_skipped += 1
            continue
        if dry_run:
            report.memories_added += 1
            existing_hashes.add(digest)
            continue
        try:
            # The whole point: vectors are derived HERE, by this host's model.
            embedding = await embedder.embed(record.content)
            await semantic.write(
                user_id,
                record.kind,
                record.content,
                embedding,
                source=_import_source(record),
                importance=record.importance,
            )
        except Exception as e:
            report.memories_failed += 1
            log.warning("vault.import.memory_failed", id=record.id, err=str(e)[:200])
            continue
        existing_hashes.add(digest)
        report.memories_added += 1

    report.identity_applied = False
    _, changed, reason = resolve_identity(bundle.identity, None)
    report.identity_applied = changed and not dry_run
    report.identity_reason = reason

    log.info(
        "vault.import.done",
        added=report.memories_added,
        skipped=report.memories_skipped,
        failed=report.memories_failed,
        dry_run=dry_run,
    )
    return report


def _import_source(record: MemoryRecord) -> str:
    """Keep provenance across the hop.

    A memory that arrived from another host should say so — otherwise this host
    will later re-export it claiming it learned it itself.
    """
    origin = record.learned_by or "unknown-host"
    base = record.source or "vault"
    return f"vault:{origin}/{base}"[:200]


async def _known_hashes(semantic: Any, user_id: UUID) -> set[str]:
    """Content hashes already in the store, for dedupe."""
    hashes: set[str] = set()
    offset = 0
    while True:
        try:
            rows = await semantic.list_recent(user_id, limit=500, offset=offset)
        except Exception as e:
            log.warning("vault.import.scan_failed", err=str(e))
            break
        if not rows:
            break
        for row in rows:
            hashes.add(content_hash(row.content))
        offset += len(rows)
        if len(rows) < 500:
            break
    return hashes
