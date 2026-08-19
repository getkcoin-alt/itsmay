"""Entity maturity — the gate between "it works" and "it is an operator".

Scrappy crosses the engineering threshold not when it can think, but when the
system can disappear, restart elsewhere, reconstruct its operational identity and
state, understand what it was doing, and safely continue.

`acceptance` turns that into twenty checks with evidence, and an `ENTITY_STATUS`
that is only TRUE when every one of them passes.
"""

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

__all__ = [
    "CRITERIA",
    "Criterion",
    "Finding",
    "Method",
    "Report",
    "Result",
    "Verdict",
    "run_acceptance",
]
