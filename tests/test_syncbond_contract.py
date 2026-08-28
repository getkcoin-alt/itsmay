import pytest
from pydantic import ValidationError

from core.contracts.syncbond import (
    ActorKind,
    EventType,
    Objective,
    ResolutionState,
    SYNCBOND_VERSION,
    envelope,
)


def test_vault_can_emit_objective_without_execution_authority() -> None:
    event = envelope(
        actor_id="service:vault-zeta",
        actor_kind=ActorKind.SERVICE,
        event_type=EventType.OBJECTIVE_REQUESTED,
        source="vault-zeta",
        payload=Objective(
            statement="Inspect the current machine state",
            max_risk="read",
            success_criteria=["return evidence-backed status"],
        ),
    )

    assert event.schema_version == SYNCBOND_VERSION
    assert event.event_type is EventType.OBJECTIVE_REQUESTED
    assert event.payload["max_risk"] == "read"
    assert event.resolution is ResolutionState.KNOWN


def test_vault_does_not_accept_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        envelope(
            actor_id="service:vault-zeta",
            actor_kind=ActorKind.SERVICE,
            event_type=EventType.EXPERIENCE_RECORDED,
            source="vault-zeta",
            payload={"summary": "unverified"},
            confidence=1.5,
        )
