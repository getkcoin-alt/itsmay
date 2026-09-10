"""Typed contracts for Scrappy's continuous-presence event loop."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DecisionKind(StrEnum):
    """What the presence layer decided should happen next.

    V1 executes SILENCE/SPEAK only as an attention decision. ACT and the other
    values are reserved contracts for later policy-gated orchestration; the
    presence runtime never turns them into consequential side effects by itself.
    """

    SILENCE = "SILENCE"
    SPEAK = "SPEAK"
    ACT = "ACT"
    ASK_APPROVAL = "ASK_APPROVAL"
    DEFER = "DEFER"
    ESCALATE = "ESCALATE"


class PresenceEvent(BaseModel):
    """One normalized observation entering Scrappy's continuous runtime."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=120)
    actor: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)
    sensitivity: str = "normal"
    domain: str = "general"
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    goal_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    interruption_cost: float = Field(default=0.5, ge=0.0, le=1.0)
    correlation_id: str | None = None
    causation_id: str | None = None
    device_id: str | None = None
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class PresenceDecision(BaseModel):
    """Auditable output from the deterministic initiative pre-filter."""

    event_id: str
    trace_id: str
    decision: DecisionKind
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    timestamp: datetime = Field(default_factory=_utcnow)


class PresenceUtterance(BaseModel):
    """A non-consequential proactive message waiting for an output client.

    V1 only creates an utterance when an accepted event both crosses the SPEAK
    threshold and explicitly carries a human-readable `payload.message`. This
    keeps the presence layer deterministic: it never invents text or silently
    calls an LLM just because a heartbeat fired.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_id: str
    trace_id: str
    text: str = Field(min_length=1, max_length=4000)
    source: str
    domain: str
    timestamp: datetime = Field(default_factory=_utcnow)
