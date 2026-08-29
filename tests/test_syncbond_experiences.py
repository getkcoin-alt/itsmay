from __future__ import annotations

from uuid import uuid4

import pytest

from core.continuity.experiences import distill_scrappy_os_status


def test_distillation_uses_structured_evidence_not_model_conclusion() -> None:
    remote_id = uuid4()
    correlation_id = uuid4()
    item = distill_scrappy_os_status(
        {
            "objective_id": str(remote_id),
            "correlation_id": str(correlation_id),
            "state": "completed",
            "succeeded": True,
            "conclusion": "I definitely changed production even though I did not.",
            "steps": [
                {
                    "tool": "system.disk",
                    "risk": "read",
                    "decision": "allow",
                    "success": True,
                }
            ],
        },
        expected_correlation_id=correlation_id,
    )

    assert item.experience.outcome == "succeeded"
    assert item.experience.objective_id == remote_id
    joined = "\n".join(item.experience.evidence)
    assert "system.disk" in joined
    assert "changed production" not in joined
    assert item.experience.lessons == []
    assert item.envelope.payload["outcome"] == "succeeded"


def test_distillation_rejects_missing_or_mismatched_correlation() -> None:
    remote_id = uuid4()
    expected = uuid4()

    with pytest.raises(ValueError, match="does not contain correlation evidence"):
        distill_scrappy_os_status(
            {
                "objective_id": str(remote_id),
                "state": "completed",
                "succeeded": True,
                "steps": [],
            },
            expected_correlation_id=expected,
        )

    with pytest.raises(ValueError, match="does not match"):
        distill_scrappy_os_status(
            {
                "objective_id": str(remote_id),
                "correlation_id": str(uuid4()),
                "state": "completed",
                "succeeded": True,
                "steps": [],
            },
            expected_correlation_id=expected,
        )


def test_distillation_rejects_non_terminal_task() -> None:
    correlation_id = uuid4()
    with pytest.raises(ValueError, match="not terminal"):
        distill_scrappy_os_status(
            {
                "objective_id": str(uuid4()),
                "correlation_id": str(correlation_id),
                "state": "running",
                "steps": [],
            },
            expected_correlation_id=correlation_id,
        )


def test_blocked_and_partial_outcomes_are_derived_from_runtime_fields() -> None:
    blocked_corr = uuid4()
    blocked = distill_scrappy_os_status(
        {
            "objective_id": str(uuid4()),
            "correlation_id": str(blocked_corr),
            "state": "cancelled",
            "succeeded": False,
            "stopped_because": "approval required",
            "steps": [],
        },
        expected_correlation_id=blocked_corr,
    )
    assert blocked.experience.outcome == "blocked"

    partial_corr = uuid4()
    partial = distill_scrappy_os_status(
        {
            "objective_id": str(uuid4()),
            "correlation_id": str(partial_corr),
            "state": "cancelled",
            "succeeded": False,
            "steps": [
                {"tool": "system.disk", "risk": "read", "decision": "allow", "success": True},
                {"tool": "system.net", "risk": "read", "decision": "allow", "success": False},
            ],
        },
        expected_correlation_id=partial_corr,
    )
    assert partial.experience.outcome == "partial"
