"""The entity acceptance suite — and the rules that keep it honest.

Two kinds of test here. The first kind checks the *machinery*: that a probe
which lies gets capped, that a probe which explodes fails instead of crashing the
run, that ENTITY_STATUS cannot be reached on partial credit. The second kind
pins the *current* verdicts, so a regression in a passing criterion breaks the
build rather than quietly downgrading Scrappy.

The suite is a gate. A gate that can be argued down is decoration.
"""

from __future__ import annotations

import json

import pytest

from core.entity.acceptance import (
    CRITERIA,
    Criterion,
    Finding,
    Method,
    Report,
    Result,
    Verdict,
    run_acceptance,
)


@pytest.fixture(scope="module")
def report() -> Report:
    return run_acceptance()


# ── the honesty rules ─────────────────────────────────────────────────


def test_structural_evidence_can_never_claim_a_pass():
    # "A planner module exists" is not "it plans". Existence is not behaviour.
    claimed = Finding(Verdict.PASS, "core.goals is importable")
    capped = claimed.capped_for(Method.STRUCTURAL)
    assert capped.verdict is Verdict.PARTIAL
    assert "structural evidence only" in capped.evidence


def test_a_soak_cannot_be_self_certified():
    capped = Finding(Verdict.PASS, "looks stable").capped_for(Method.ATTESTED)
    assert capped.verdict is Verdict.PARTIAL
    assert "soak record" in capped.evidence


def test_runtime_evidence_is_left_alone():
    proven = Finding(Verdict.PASS, "actually exercised it")
    assert proven.capped_for(Method.RUNTIME) is proven


def test_capping_never_upgrades_a_verdict():
    for method in Method:
        for verdict in (Verdict.FAIL, Verdict.PARTIAL):
            assert Finding(verdict, "x").capped_for(method).verdict is verdict


def test_a_broken_probe_fails_loudly_rather_than_crashing_the_run():
    def explodes() -> Finding:
        raise RuntimeError("probe is broken")

    c = Criterion("boom", "Broken", "L4", Method.RUNTIME, explodes)
    finding = c.run()
    assert finding.verdict is Verdict.FAIL
    assert "RuntimeError" in finding.evidence and "probe is broken" in finding.evidence


def test_entity_status_needs_every_criterion_not_most(report: Report):
    passing = Finding(Verdict.PASS, "ok")
    all_pass = Report(
        results=[Result(c, passing) for c in CRITERIA[:3]]
    )
    assert all_pass.entity_status is True

    # One partial is enough to hold the gate shut. No partial credit.
    nearly = Report(
        results=[
            Result(CRITERIA[0], passing),
            Result(CRITERIA[1], Finding(Verdict.PARTIAL, "almost")),
        ]
    )
    assert nearly.entity_status is False
    assert Report(results=[]).entity_status is False  # nothing proven is not a pass


def test_level_stops_at_the_first_incomplete_tier():
    passing, failing = Finding(Verdict.PASS, "ok"), Finding(Verdict.FAIL, "no")
    by_level = {c.level: c for c in CRITERIA}
    # L2 complete, L3 not → cannot claim L3, and must not skip to L4.
    partial = Report(
        results=[
            Result(by_level["L2"], passing),
            Result(by_level["L3"], failing),
            Result(by_level["L4"], passing),
        ]
    )
    assert partial.level() == "L2"


# ── the suite's shape ─────────────────────────────────────────────────


def test_covers_all_twenty_criteria():
    assert len(CRITERIA) == 20
    assert len({c.id for c in CRITERIA}) == 20  # no duplicate ids


def test_every_criterion_declares_a_real_level():
    assert {c.level for c in CRITERIA} <= {"L2", "L3", "L4"}


def test_every_finding_carries_evidence(report: Report):
    # A verdict without a reason is unfalsifiable, which defeats the point.
    for res in report.results:
        assert res.finding.evidence.strip(), f"{res.criterion.id} gave no evidence"
        assert len(res.finding.evidence) > 20, f"{res.criterion.id}: evidence too thin"


def test_report_serialises_for_ci(report: Report):
    blob = json.dumps(report.to_dict())
    data = json.loads(blob)
    assert data["entity_status"] is report.entity_status
    assert len(data["criteria"]) == 20
    assert set(data["counts"]) == {"PASS", "PARTIAL", "FAIL"}


def test_running_the_suite_has_no_lasting_side_effects():
    """The freeze probe flips a real switch; it must put it back."""
    from core.identity.self_guard import is_frozen

    before = is_frozen()
    run_acceptance()
    assert is_frozen() is before


# ── current verdicts (a regression here is a real regression) ─────────


def test_entity_status_is_false_and_honestly_so(report: Report):
    # Scrappy is not an entity yet. If this ever flips, it must be because all
    # twenty passed — not because someone softened a probe.
    assert report.entity_status is False
    assert report.counts["FAIL"] > 0


@pytest.mark.parametrize(
    "criterion_id",
    [
        "memory-migration",  # vault export → import round-trip
        "mission-model-swap",  # identity carries no model fingerprint
        "tool-routing",  # tools chosen from the registry by name
        "detects-failure",  # a raising tool is captured and fed back
        "rejects-unauthorised",  # approval gate refuses server-side
        "no-privilege-escalation",  # frozenset grant from the request
        "selfmod-validated",  # guard consulted before merge
        "selfmod-rollback",  # rollback path exists in the apply script
        "revocable-authority",  # freeze takes effect immediately
    ],
)
def test_these_capabilities_are_proven_and_must_stay_proven(
    report: Report, criterion_id: str
):
    found = next(r for r in report.results if r.criterion.id == criterion_id)
    assert found.finding.verdict is Verdict.PASS, (
        f"{criterion_id} regressed: {found.finding.evidence}"
    )


@pytest.mark.parametrize(
    "criterion_id,because",
    [
        ("resume-objectives", "no persisted objective store"),
        ("plans-objectives", "no planner"),
        ("recovery-strategy", "no recovery layer"),
        ("independent-verify", "no verifier"),
        ("resource-ceilings", "no resource ledger"),
        ("actions-attributable", "audit covers self-modification only"),
        ("goal-provenance", "no goal model"),
        ("soak-7d", "nothing schedules autonomous work"),
    ],
)
def test_known_gaps_are_reported_as_gaps(report: Report, criterion_id: str, because: str):
    """These are the specs for the next builds. When one starts passing, delete
    its line here — that deletion is the record that the gap closed."""
    found = next(r for r in report.results if r.criterion.id == criterion_id)
    assert found.finding.verdict is Verdict.FAIL
    assert because in found.finding.evidence
