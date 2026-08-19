# Entity maturity — the ENTITY_STATUS gate

> Scrappy does not become an entity when it can think. Scrappy crosses the
> engineering threshold when the system can disappear, restart elsewhere,
> reconstruct its operational identity and state, understand what it was doing,
> decide what should happen next, and safely continue.

That is a testable claim, so it is tested. `scrappy entity` runs twenty criteria
against this checkout and prints a scorecard. `ENTITY_STATUS` is TRUE only when
every one of them passes.

```bash
scrappy entity          # the scorecard
scrappy entity --json   # machine-readable, for CI
```

## Maturity levels

| | | |
|---|---|---|
| **L0** | Assistant | Prompt in → response out |
| **L1** | Agent | Uses tools, completes multi-step tasks |
| **L2** | Persistent Agent | Identity, memory, scheduled/event-driven operation |
| **L3** | Autonomous Operator | Plans, executes, verifies, recovers, delegates |
| **L4** | Software Entity | Continuity, goals, authority boundaries, world state, self-maintenance, accountable autonomy over long periods |
| **L5** | Embodied Entity | L4 + persistent sensory presence, voice, spatial awareness, physical embodiment |

`Report.level()` reports the highest tier whose criteria *all* pass, and stops at
the first incomplete tier — it never skips ahead because a later item happens to
be green.

## The rule that keeps the gate honest

A suite you can argue down is decoration. Two rules are enforced in
`core/entity/acceptance.py`, centrally, rather than trusted to each probe:

1. **Structural evidence can never claim a PASS.** "A planner module exists" is
   not "it plans". A `Method.STRUCTURAL` probe caps at PARTIAL no matter what it
   returns.
2. **A soak cannot be self-certified.** `Method.ATTESTED` criteria pass only
   against a dated record on disk (`~/.itsmay/soak-report.json`). A system cannot
   vouch for uptime it has not had.

Only `Method.RUNTIME` — a probe that actually exercises the behaviour now — can
pass. A probe that raises is a FAIL with the traceback as its evidence, never a
skip and never a crash.

Every verdict carries **evidence**: a concrete sentence you can argue with. A FAIL
is therefore not a scolding, it is the specification for the next piece of work.

## Where Scrappy stands today

**9 pass · 2 partial · 9 fail — `ENTITY_STATUS = FALSE`.**

Proven at runtime: memory survives migration (a real export → disk → import into
a clean store), the identity carried between hosts names no model, tools are
chosen from the registry by name, a failing tool is captured and fed back, the
approval gate refuses server-side, the grant is an immutable frozenset from the
authenticated request, self-modification consults the guard and can roll itself
back, and freeze revokes authority immediately.

The gaps cluster into three missing primitives:

| Missing | Criteria it blocks |
|---|---|
| **A goal model** (objectives → DAG → tasks, persisted) | resume-objectives, plans-objectives, explains-work, goal-provenance, self-stop |
| **A resource ledger** (money, compute, disk, quota) | resource-ceilings, and makes a soak observable |
| **Universal audit** (actor → intent → decision → tool → result → verification) | actions-attributable |

Plus two that follow from the above: nothing schedules autonomous work, so there
is no soak to attest; and the persona is still hand-authored beside the vault
rather than derived from it, so two hosts can drift.

## Adding or changing a criterion

Add a `Criterion` to `CRITERIA` with a probe returning `Finding(verdict, evidence)`.
Pick the `Method` honestly — if the probe only checks that a module imports, it is
`STRUCTURAL` and will be capped, which is the correct outcome.

`tests/test_entity_acceptance.py` pins the current verdicts. When a gap closes,
its line moves from the "known gaps" list to the "must stay proven" list — that
edit is the durable record that the gap closed.
