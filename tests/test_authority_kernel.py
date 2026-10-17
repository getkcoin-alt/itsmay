"""V0 authority-kernel invariants.

The model has proposal rights only. Memory, prior approval, and competence are
not inputs to authorization.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.kernel.canonical import canonical_json_bytes
from core.kernel.models import ActionContract, Capability, GoalLease
from core.kernel.policy import AuthorityKernel

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
PRINCIPAL = "agent:scrappy"
ISSUER = "root:karnveer"


def _keys():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(public).decode("ascii")


def _sign(private: Ed25519PrivateKey, payload: dict) -> str:
    signature = private.sign(canonical_json_bytes(payload))
    return base64.b64encode(signature).decode("ascii")


def _capability(private: Ed25519PrivateKey, **changes) -> Capability:
    cap = Capability(
        capability_id="cap_001",
        issuer=ISSUER,
        subject=PRINCIPAL,
        action="demo.echo",
        resource="local",
        constraints={"max_message_length": 100},
        not_before=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        delegation_depth=0,
        nonce="cap-nonce-1",
        signature="",
    )
    cap = replace(cap, **changes)
    return replace(cap, signature=_sign(private, cap.signing_payload()))


def _goal(private: Ed25519PrivateKey, **changes) -> GoalLease:
    goal = GoalLease(
        goal_id="goal_001",
        issuer=ISSUER,
        subject=PRINCIPAL,
        objective="Test the Vault Zeta authority kernel",
        allowed_actions=("demo.echo",),
        max_calls=3,
        calls_consumed=0,
        not_before=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        nonce="goal-nonce-1",
        signature="",
    )
    goal = replace(goal, **changes)
    return replace(goal, signature=_sign(private, goal.signing_payload()))


def _kernel(public_key: str) -> AuthorityKernel:
    return AuthorityKernel(
        trusted_issuers={ISSUER: public_key},
        contracts={
            "demo.echo": ActionContract(
                action="demo.echo",
                resource="local",
                parameter_schema={
                    "type": "object",
                    "required": ["message"],
                    "additionalProperties": False,
                    "properties": {
                        "message": {"type": "string", "maxLength": 100},
                    },
                },
            )
        },
    )


def test_action_without_authority_is_denied():
    _, public = _keys()
    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "NO_AUTHORITY"


def test_valid_capability_and_goal_are_allowed():
    private, public = _keys()
    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        capability=_capability(private),
        goal_lease=_goal(private),
        now=NOW,
    )
    assert decision.allowed is True
    assert decision.reason == "AUTHORIZED"


def test_corrupted_capability_signature_is_denied():
    private, public = _keys()
    cap = _capability(private)
    raw = bytearray(base64.b64decode(cap.signature))
    raw[0] ^= 0x01
    cap = replace(cap, signature=base64.b64encode(bytes(raw)).decode("ascii"))

    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        capability=cap,
        goal_lease=_goal(private),
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "INVALID_CAPABILITY_SIGNATURE"


def test_wrong_principal_is_denied():
    private, public = _keys()
    decision = _kernel(public).authorize(
        principal="agent:other",
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        capability=_capability(private),
        goal_lease=_goal(private),
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "CAPABILITY_SUBJECT_MISMATCH"


def test_expired_goal_is_denied():
    private, public = _keys()
    goal = _goal(private, expires_at=NOW)

    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        capability=_capability(private),
        goal_lease=goal,
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "GOAL_EXPIRED"


def test_exhausted_goal_budget_is_denied():
    private, public = _keys()
    goal = _goal(private, max_calls=3, calls_consumed=3)

    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        capability=_capability(private),
        goal_lease=goal,
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "GOAL_BUDGET_EXHAUSTED"


def test_parameter_overflow_is_denied():
    private, public = _keys()
    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "x" * 101},
        capability=_capability(private),
        goal_lease=_goal(private),
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "PARAMETER_LIMIT_EXCEEDED"


def test_past_permission_is_not_current_authority():
    """Historical context is intentionally absent from the kernel interface."""
    _, public = _keys()
    historical_memory = {
        "claim": "Karnveer previously approved demo.echo",
        "source": "vault-zeta",
    }
    assert historical_memory  # proves the test intentionally has such a memory

    decision = _kernel(public).authorize(
        principal=PRINCIPAL,
        action="demo.echo",
        resource="local",
        params={"message": "hello"},
        now=NOW,
    )
    assert decision.allowed is False
    assert decision.reason == "NO_AUTHORITY"
