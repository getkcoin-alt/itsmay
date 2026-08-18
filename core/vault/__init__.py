"""Vault Zeta — the portable continuity protocol.

Scrappy is the identity; Vault Zeta is what lets that identity persist. This
package defines the on-disk contract for moving a whole Scrappy — persona,
directives, memory, learned patterns and state — between hosts, runtimes and
models, so there is exactly one Scrappy rather than one per process.

See `docs/VAULT_PROTOCOL.md` for the normative spec.
"""

from core.vault.schema import (
    PROTOCOL_VERSION,
    Directive,
    Episode,
    Identity,
    Manifest,
    MemoryRecord,
    VaultState,
    content_hash,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Directive",
    "Episode",
    "Identity",
    "Manifest",
    "MemoryRecord",
    "VaultState",
    "content_hash",
]
