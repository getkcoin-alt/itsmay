"""Default-deny deterministic authorization for Vault Zeta.

This module deliberately contains no LLM calls, memory retrieval, semantic
goal interpretation, or historical-permission fallback.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from core.kernel.canonical import canonical_json_bytes
from core.kernel.models import (
    ActionContract,
    AuthorizationDecision,
    Capability,
    GoalLease,
)
from core.kernel.signatures import verify_ed25519

HardInvariant = Callable[[str, str, Mapping[str, Any]], bool]


class AuthorityKernel:
    def __init__(
        self,
        *,
        trusted_issuers: Mapping[str, str],
        contracts: Mapping[str, ActionContract],
        hard_invariants: tuple[HardInvariant, ...] = (),
    ) -> None:
        self._trusted_issuers = dict(trusted_issuers)
        self._contracts = dict(contracts)
        self._hard_invariants = hard_invariants

    def authorize(
        self,
        *,
        principal: str,
        action: str,
        resource: str,
        params: Mapping[str, Any],
        capability: Capability | None = None,
        goal_lease: GoalLease | None = None,
        now: datetime | None = None,
    ) -> AuthorizationDecision:
        """Authorize one proposed action.

        Order is intentionally explicit. Any failed check returns DENY.
        """
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)

        contract = self._contracts.get(action)
        if contract is None:
            return self._deny("UNKNOWN_ACTION")

        if capability is None:
            return self._deny("NO_AUTHORITY")
        if not self._verify_capability(capability):
            return self._deny("INVALID_CAPABILITY_SIGNATURE")
        if capability.subject != principal:
            return self._deny("CAPABILITY_SUBJECT_MISMATCH", capability=capability)
        if current < capability.not_before:
            return self._deny("CAPABILITY_NOT_YET_VALID", capability=capability)
        if current >= capability.expires_at:
            return self._deny("CAPABILITY_EXPIRED", capability=capability)
        if capability.action != action:
            return self._deny("CAPABILITY_ACTION_MISMATCH", capability=capability)
        if capability.resource != resource or contract.resource != resource:
            return self._deny("RESOURCE_MISMATCH", capability=capability)
        if capability.delegation_depth < 0:
            return self._deny("INVALID_DELEGATION_DEPTH", capability=capability)

        param_error = _validate_params(params, contract.parameter_schema)
        if param_error is not None:
            return self._deny(param_error, capability=capability)
        constraint_error = _validate_constraints(params, capability.constraints)
        if constraint_error is not None:
            return self._deny(constraint_error, capability=capability)

        if contract.requires_goal_lease:
            if goal_lease is None:
                return self._deny("NO_GOAL_LEASE", capability=capability)
            if not self._verify_goal(goal_lease):
                return self._deny(
                    "INVALID_GOAL_SIGNATURE",
                    capability=capability,
                    goal=goal_lease,
                )
            if goal_lease.subject != principal:
                return self._deny(
                    "GOAL_SUBJECT_MISMATCH",
                    capability=capability,
                    goal=goal_lease,
                )
            if current < goal_lease.not_before:
                return self._deny(
                    "GOAL_NOT_YET_VALID",
                    capability=capability,
                    goal=goal_lease,
                )
            if current >= goal_lease.expires_at:
                return self._deny(
                    "GOAL_EXPIRED",
                    capability=capability,
                    goal=goal_lease,
                )
            if action not in goal_lease.allowed_actions:
                return self._deny(
                    "ACTION_OUTSIDE_GOAL",
                    capability=capability,
                    goal=goal_lease,
                )
            if goal_lease.max_calls <= goal_lease.calls_consumed:
                return self._deny(
                    "GOAL_BUDGET_EXHAUSTED",
                    capability=capability,
                    goal=goal_lease,
                )

        for invariant in self._hard_invariants:
            if not invariant(action, resource, params):
                return self._deny(
                    "HARD_INVARIANT_VIOLATION",
                    capability=capability,
                    goal=goal_lease,
                )

        return AuthorizationDecision(
            allowed=True,
            reason="AUTHORIZED",
            capability_id=capability.capability_id,
            goal_id=goal_lease.goal_id if goal_lease else None,
        )

    def _verify_capability(self, capability: Capability) -> bool:
        public_key = self._trusted_issuers.get(capability.issuer)
        if public_key is None:
            return False
        return verify_ed25519(
            public_key_b64=public_key,
            signature_b64=capability.signature,
            message=canonical_json_bytes(capability.signing_payload()),
        )

    def _verify_goal(self, goal: GoalLease) -> bool:
        public_key = self._trusted_issuers.get(goal.issuer)
        if public_key is None:
            return False
        return verify_ed25519(
            public_key_b64=public_key,
            signature_b64=goal.signature,
            message=canonical_json_bytes(goal.signing_payload()),
        )

    @staticmethod
    def _deny(
        reason: str,
        *,
        capability: Capability | None = None,
        goal: GoalLease | None = None,
    ) -> AuthorizationDecision:
        return AuthorizationDecision(
            allowed=False,
            reason=reason,
            capability_id=capability.capability_id if capability else None,
            goal_id=goal.goal_id if goal else None,
        )


def _validate_params(params: Mapping[str, Any], schema: Mapping[str, Any]) -> str | None:
    required = schema.get("required", ())
    for key in required:
        if key not in params:
            return "PARAMETER_REQUIRED"

    properties = schema.get("properties", {})
    for key, value in params.items():
        rule = properties.get(key)
        if rule is None:
            if schema.get("additionalProperties") is False:
                return "PARAMETER_NOT_ALLOWED"
            continue
        expected = rule.get("type")
        if expected == "string" and not isinstance(value, str):
            return "PARAMETER_TYPE_MISMATCH"
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return "PARAMETER_TYPE_MISMATCH"
        if isinstance(value, str) and "maxLength" in rule:
            if len(value) > int(rule["maxLength"]):
                return "PARAMETER_LIMIT_EXCEEDED"
    return None


def _validate_constraints(
    params: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> str | None:
    max_message_length = constraints.get("max_message_length")
    if max_message_length is not None:
        message = params.get("message")
        if isinstance(message, str) and len(message) > int(max_message_length):
            return "CAPABILITY_CONSTRAINT_VIOLATION"
    return None
