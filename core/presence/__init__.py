"""Continuous Presence primitives for Scrappy.

This package is intentionally small: it provides real event intake, heartbeat,
initiative pre-filtering, and truthful runtime status. It does not claim
consciousness and it does not grant permission to perform consequential work.
"""

from core.presence.models import DecisionKind, PresenceDecision, PresenceEvent
from core.presence.runtime import PresenceRuntime, get_presence_runtime

__all__ = [
    "DecisionKind",
    "PresenceDecision",
    "PresenceEvent",
    "PresenceRuntime",
    "get_presence_runtime",
]
