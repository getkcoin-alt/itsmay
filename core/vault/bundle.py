"""Reading and writing a vault bundle on disk.

A bundle is a directory, not a single blob: JSONL for the collections that grow
(memories, directives, episodes) and JSON for the small singletons. That choice
buys line-level diffs in git, streaming reads on a large vault, and appends that
don't rewrite the world.

Everything here is pure filesystem + JSON. No database, no embedder, no network —
so a consumer in another repo can reimplement it from the spec in an afternoon,
which is the point of having a protocol rather than a shared library.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from core.logging import get_logger
from core.vault.schema import (
    Directive,
    Episode,
    Identity,
    Manifest,
    MemoryRecord,
    VaultModel,
    VaultState,
    check_compatible,
)

log = get_logger(__name__)

T = TypeVar("T", bound=VaultModel)


class MalformedVault(Exception):
    """The directory isn't a readable bundle."""


def _dump(model: VaultModel) -> str:
    """Serialize one record. `mode="json"` keeps datetimes as ISO strings so the
    file is readable by anything, not just pydantic."""
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def write_json(path: Path, model: VaultModel) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, models: Iterable[VaultModel]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for model in models:
            fh.write(_dump(model) + "\n")
            n += 1
    return n


def read_json(path: Path, model_cls: type[T]) -> T:
    try:
        return model_cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise MalformedVault(f"missing {path.name}") from None
    except (json.JSONDecodeError, ValueError) as e:
        raise MalformedVault(f"{path.name} is not valid: {e}") from None


def read_jsonl(path: Path, model_cls: type[T]) -> Iterator[T]:
    """Stream records. A single bad line is skipped, not fatal — one corrupt
    memory must not cost you the other nine hundred."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield model_cls.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError) as e:
                log.warning(
                    "vault.bad_record", file=path.name, line=lineno, err=str(e)[:200]
                )


@dataclass(slots=True)
class VaultBundle:
    """A whole Scrappy, in memory."""

    manifest: Manifest
    identity: Identity
    directives: list[Directive] = field(default_factory=list)
    memories: list[MemoryRecord] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    state: VaultState = field(default_factory=VaultState)

    # ── disk ─────────────────────────────────────────────────────
    def write(self, directory: Path) -> Path:
        """Write the bundle to `directory`, creating it if needed."""
        directory.mkdir(parents=True, exist_ok=True)
        self.manifest.counts = {
            "memories": len(self.memories),
            "directives": len(self.directives),
            "episodes": len(self.episodes),
        }
        write_json(directory / "identity.json", self.identity)
        write_json(directory / "state.json", self.state)
        write_jsonl(directory / "directives.jsonl", self.directives)
        write_jsonl(directory / "memories.jsonl", self.memories)
        write_jsonl(directory / "episodes.jsonl", self.episodes)
        # Manifest last: its presence means the rest is fully written.
        write_json(directory / "manifest.json", self.manifest)
        log.info("vault.written", path=str(directory), **self.manifest.counts)
        return directory

    @classmethod
    def read(cls, directory: Path) -> VaultBundle:
        """Load a bundle. Refuses an unknown major before reading anything else."""
        directory = Path(directory)
        if not directory.is_dir():
            raise MalformedVault(f"{directory} is not a directory")
        manifest = read_json(directory / "manifest.json", Manifest)
        check_compatible(manifest.protocol_version)  # raises IncompatibleVault
        return cls(
            manifest=manifest,
            identity=read_json(directory / "identity.json", Identity),
            directives=list(read_jsonl(directory / "directives.jsonl", Directive)),
            memories=list(read_jsonl(directory / "memories.jsonl", MemoryRecord)),
            episodes=list(read_jsonl(directory / "episodes.jsonl", Episode)),
            state=(
                read_json(directory / "state.json", VaultState)
                if (directory / "state.json").exists()
                else VaultState()
            ),
        )

    def summary(self) -> dict[str, Any]:
        """One-glance description, for CLI output and the API."""
        return {
            "protocol_version": self.manifest.protocol_version,
            "vault_id": self.manifest.vault_id,
            "exported_at": _iso(self.manifest.exported_at),
            "exported_by": self.manifest.exported_by,
            "identity": self.identity.name,
            "operator": self.identity.operator.handle,
            "memories": len(self.memories),
            "directives": len(self.directives),
            "episodes": len(self.episodes),
            "includes_episodes": self.manifest.includes_episodes,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
