from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from core.continuity.evidence_bundle import export_experience_bundle, verify_bundle_hash
from core.continuity.experiences import DistilledExperience
from core.contracts.syncbond import (
    ActorKind,
    EventType,
    Experience,
    ProvenanceRef,
    envelope,
)

CORRELATION = UUID("11111111-1111-4111-8111-111111111111")
OBJECTIVE = UUID("22222222-2222-4222-8222-222222222222")


def _item() -> DistilledExperience:
    experience = Experience(
        objective_id=OBJECTIVE,
        summary="Bounded read task failed after one verified step.",
        outcome="failed",
        evidence=["state=failed", "verified_steps=1", "failed_steps=1"],
        lessons=[],
    )
    recorded = envelope(
        actor_id="service:vault-zeta",
        actor_kind=ActorKind.SERVICE,
        event_type=EventType.EXPERIENCE_RECORDED,
        source="vault-zeta",
        payload=experience,
        correlation_id=CORRELATION,
        provenance=[
            ProvenanceRef(
                source="scrappy-os",
                observed_at=datetime(2026, 8, 29, 0, 0, tzinfo=UTC),
                reference=f"/tasks/{OBJECTIVE}",
            )
        ],
    )
    # Pin normally generated envelope fields so repeated construction is stable.
    recorded.event_id = UUID("33333333-3333-4333-8333-333333333333")
    recorded.created_at = datetime(2026, 8, 29, 0, 1, tzinfo=UTC)
    return DistilledExperience(CORRELATION, OBJECTIVE, experience, recorded)


def test_bundle_is_content_addressed_and_deterministic():
    first = export_experience_bundle(_item())
    second = export_experience_bundle(_item())

    assert first == second
    assert first["protocol"] == "SYNCBOND"
    assert first["schema_version"] == "5.0.0"
    assert first["correlation_id"] == str(CORRELATION)
    assert first["remote_objective_id"] == str(OBJECTIVE)
    assert first["envelope"]["event_type"] == "experience.recorded"
    assert verify_bundle_hash(first) is True


def test_tampering_invalidates_bundle_hash():
    bundle = export_experience_bundle(_item())
    tampered = deepcopy(bundle)
    tampered["envelope"]["payload"]["outcome"] = "succeeded"

    assert verify_bundle_hash(tampered) is False


def test_bundle_exports_only_distilled_evidence_surface():
    bundle = export_experience_bundle(_item())
    rendered = str(bundle).lower()

    assert "password" not in rendered
    assert "credential" not in rendered
    assert "raw_log" not in rendered
    assert set(bundle) == {
        "bundle_format",
        "protocol",
        "schema_version",
        "correlation_id",
        "remote_objective_id",
        "envelope",
        "bundle_sha256",
    }
