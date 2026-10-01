"""Typed contracts for the Vault Zeta authority kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    issuer: str
    subject: str
    action: str
    resource: str
    constraints: dict[str, Any]
    not_before: datetime
    expires_at: datetime
    delegation_depth: int
    nonce: str
    signature: str

    def signing_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "action": self.action,
            "resource": self.resource,
            "constraints": self.constraints,
            "not_before": _iso(self.not_before),
            "expires_at": _iso(self.expires_at),
            "delegation_depth": self.delegation_depth,
            "nonce": self.nonce,
        }


@dataclass(frozen=True, slots=True)
class GoalLease:
    goal_id: str
    issuer: str
    subject: str
    objective: str
    allowed_actions: tuple[str, ...]
    max_calls: int
    calls_consumed: int
    not_before: datetime
    expires_at: datetime
    nonce: str
    signature: str

    def signing_payload(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "objective": self.objective,
            "allowed_actions": list(self.allowed_actions),
            "max_calls": self.max_calls,
            "calls_consumed": self.calls_consumed,
            "not_before": _iso(self.not_before),
            "expires_at": _iso(self.expires_at),
            "nonce": self.nonce,
        }


@dataclass(frozen=True, slots=True)
class ActionContract:
    action: str
    resource: str
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    requires_goal_lease: bool = True


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    capability_id: str | None = None
    goal_id: str | None = None
