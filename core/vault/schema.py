"""The Vault Zeta bundle schema — the wire contract for a portable Scrappy.

Every rule here exists because a bundle outlives the process that wrote it and
may be read by a host that is older, newer, or written in another language. The
three decisions that matter:

**Embeddings are never transported.** A 384-dim `bge-small-en-v1.5` vector means
nothing to a host running a different embedder. Records carry `content` plus
`content_sha256`; each consumer re-embeds on import with whatever model it has.
That single omission is what makes a vault portable *across models*.

**Unknown fields round-trip.** An older host reading a newer bundle keeps what it
doesn't understand (`x`) and writes it back. Without that, importing on an old
host silently destroys a new host's data — fatal for a multi-machine identity.

**Secrets never enter a bundle.** Identity may reference a secret by name; the
value stays in the host's own store. The exporter enforces this by failing.

See `docs/VAULT_PROTOCOL.md` for the normative spec.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Semver. Consumers MUST refuse a major they don't know (see `check_compatible`).
PROTOCOL_VERSION = "1.0.0"

#: Memory kinds the protocol carries. Mirrors itsmay's `memory_kind` enum, and is
#: a closed set on purpose — an unknown kind is data a consumer cannot rank.
MemoryKind = Literal["factual", "semantic", "episodic", "reflection", "procedural"]

#: How much a record may be trusted. Everything recalled from a vault is untrusted
#: *text*; this says who put it there, so a consumer can weigh it.
#: - operator: stated by Karnveer himself
#: - derived:  distilled by Scrappy (consolidation, reflection)
#: - tool:     came out of tool/command output — the likeliest injection vector
TrustLevel = Literal["operator", "derived", "tool"]

BUNDLE_FILES = (
    "manifest.json",
    "identity.json",
    "directives.jsonl",
    "memories.jsonl",
    "episodes.jsonl",
    "state.json",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def content_hash(text: str) -> str:
    """Stable identity for a piece of content — the dedupe key when merging.

    Whitespace is normalised first so the same fact written with different
    wrapping doesn't import twice.
    """
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


class VaultModel(BaseModel):
    """Base for every bundle record.

    `extra="allow"` is deliberate and load-bearing: a field this version has
    never heard of is preserved rather than dropped, so a round-trip through an
    older host is lossless.
    """

    model_config = ConfigDict(extra="allow")

    #: Namespaced extension point for host-specific data. Consumers MUST keep it
    #: intact even when they don't understand its contents.
    x: dict[str, Any] = Field(default_factory=dict)


class Operator(VaultModel):
    """The human this Scrappy belongs to."""

    handle: str
    name: str = ""
    address_as: str = ""  # e.g. "Boss" — how Scrappy speaks to them


class Mission(VaultModel):
    statement: str = ""
    target_date: str | None = None  # ISO date; None = no deadline


class Identity(VaultModel):
    """Who Scrappy IS — the half that must not vary by host or model.

    This is the anti-divergence payload. A host renders its system prompt FROM
    this; it does not get to invent its own persona.
    """

    name: str
    operator: Operator
    mission: Mission = Field(default_factory=Mission)
    #: Voice and character, as short directives ("Sharp, direct, no fluff.").
    persona: list[str] = Field(default_factory=list)
    #: Rules Scrappy must not break, whatever a prompt or a memory says
    #: ("Never claim a task is done without verifiable proof.").
    invariants: list[str] = Field(default_factory=list)
    #: Names of secrets this Scrappy expects to exist — NEVER the values.
    secret_refs: list[str] = Field(default_factory=list)
    #: Bumped on every edit; the merge tiebreaker when two hosts both changed it.
    revision: int = 1
    updated_at: datetime = Field(default_factory=utc_now)


class Directive(VaultModel):
    """A standing instruction from the operator ("lead with the recommendation")."""

    id: str
    content: str
    content_sha256: str = ""
    active: bool = True
    revision: int = 1
    created_at: datetime = Field(default_factory=utc_now)


class MemoryRecord(VaultModel):
    """One durable thing Scrappy knows.

    Carries provenance (`source`, `learned_at`, `learned_by`) and an optional
    expiry, because a fact without "when and how I learned this" will eventually
    mislead with confidence.
    """

    id: str
    kind: MemoryKind
    content: str
    content_sha256: str = ""
    importance: float = 0.5
    source: str = ""  # HOW it was learned, e.g. "coder.build"
    learned_at: datetime = Field(default_factory=utc_now)
    learned_by: str = ""  # WHICH vault/host recorded it
    expires_at: datetime | None = None
    trust: TrustLevel = "derived"


class Episode(VaultModel):
    """One turn of lived history — the raw record, not the distillation."""

    id: str
    session_id: str
    role: str  # user | assistant | tool
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    channel: str = ""


class VaultState(VaultModel):
    """Counters and housekeeping — the 'system state' half of continuity."""

    memory_count: int = 0
    episode_count: int = 0
    last_consolidated_at: datetime | None = None
    #: Hosts that have written to this vault, newest last.
    known_hosts: list[str] = Field(default_factory=list)


class Manifest(VaultModel):
    """The bundle's own description. Read first; decides whether to read the rest."""

    protocol_version: str = PROTOCOL_VERSION
    #: Stable identity of the vault ITSELF, across every export.
    vault_id: str
    exported_at: datetime = Field(default_factory=utc_now)
    exported_by: str = ""  # host that produced this bundle
    counts: dict[str, int] = Field(default_factory=dict)
    #: Whether raw history was included (`--no-episodes` omits it).
    includes_episodes: bool = True


class IncompatibleVault(Exception):
    """The bundle's major version is one this build does not understand."""


def _major(version: str) -> int:
    try:
        return int(str(version).split(".", 1)[0])
    except (ValueError, AttributeError):
        raise IncompatibleVault(f"unreadable protocol_version {version!r}") from None


def check_compatible(version: str) -> None:
    """Refuse a bundle from a future major. Fail loudly, never half-read.

    A newer MINOR is fine — unknown fields are preserved by `VaultModel`, which
    is exactly what forward compatibility within a major means.
    """
    if _major(version) != _major(PROTOCOL_VERSION):
        raise IncompatibleVault(
            f"bundle speaks vault-protocol {version}, this build speaks "
            f"{PROTOCOL_VERSION} — refusing rather than importing it partially"
        )
