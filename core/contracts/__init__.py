"""Cross-service protocol contracts for Vault Zeta."""

from core.contracts.syncbond import (
    ActorKind,
    EventType,
    ResolutionState,
    SyncEnvelope,
    envelope,
)

__all__ = [
    "ActorKind",
    "EventType",
    "ResolutionState",
    "SyncEnvelope",
    "envelope",
]
