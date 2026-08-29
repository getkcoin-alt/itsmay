"""Durable, evidence-first SYNCBOND Experience records.

Scrappy OS owns raw operational audit. Vault Zeta stores only a distilled
Experience after a terminal task response proves the expected correlation id.
No model-generated conclusion is treated as evidence of an action succeeding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from core.contracts.syncbond import (
    ActorKind,
    EventType,
    Experience,
    ProvenanceRef,
    SyncEnvelope,
    envelope,
)
from core.memory.db import get_pool

Outcome = Literal["succeeded", "failed", "partial", "blocked"]
_TERMINAL_STATES = {"completed", "failed", "cancelled", "crashed"}
_BLOCK_MARKERS = ("approval", "blocked", "denied", "policy", "permission")


@dataclass(slots=True, frozen=True)
class DistilledExperience:
    correlation_id: UUID
    remote_objective_id: UUID
    experience: Experience
    envelope: SyncEnvelope


def _outcome(remote: dict[str, Any]) -> Outcome:
    state = str(remote.get("state") or "").strip().lower()
    if state not in _TERMINAL_STATES:
        raise ValueError(f"Scrappy OS task is not terminal: {state or 'unknown'}")

    if remote.get("succeeded") is True:
        return "succeeded"
    if state in {"crashed", "failed"}:
        return "failed"

    stopped = str(remote.get("stopped_because") or "").lower()
    if any(marker in stopped for marker in _BLOCK_MARKERS):
        return "blocked"

    steps = remote.get("steps")
    if isinstance(steps, list) and any(isinstance(step, dict) and step.get("success") is True for step in steps):
        return "partial"
    return "failed"


def distill_scrappy_os_status(
    remote: dict[str, Any],
    *,
    expected_correlation_id: UUID,
) -> DistilledExperience:
    """Validate terminal remote evidence and turn it into a deterministic Experience."""

    remote_id_text = str(remote.get("objective_id") or "").strip()
    if not remote_id_text:
        raise ValueError("Scrappy OS status is missing objective_id")
    try:
        remote_id = UUID(remote_id_text)
    except ValueError as exc:
        raise ValueError("Scrappy OS objective_id is not a UUID") from exc

    echoed_text = str(remote.get("correlation_id") or "").strip()
    if not echoed_text:
        raise ValueError("Scrappy OS status does not contain correlation evidence")
    try:
        echoed = UUID(echoed_text)
    except ValueError as exc:
        raise ValueError("Scrappy OS returned an invalid correlation_id") from exc
    if echoed != expected_correlation_id:
        raise ValueError("Scrappy OS correlation_id does not match the originating objective")

    outcome = _outcome(remote)
    raw_steps = remote.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    verified_steps = [step for step in steps if isinstance(step, dict)]
    successes = sum(step.get("success") is True for step in verified_steps)
    failures = sum(step.get("success") is False for step in verified_steps)

    evidence = [
        f"remote_objective_id={remote_id}",
        f"state={str(remote.get('state') or '').lower()}",
        f"succeeded={remote.get('succeeded')!r}",
        f"verified_steps={len(verified_steps)}",
        f"successful_steps={successes}",
        f"failed_steps={failures}",
    ]
    stopped = str(remote.get("stopped_because") or "").strip()
    if stopped:
        evidence.append(f"stopped_because={stopped[:500]}")

    for index, step in enumerate(verified_steps[:50], start=1):
        tool = str(step.get("tool") or "unknown")[:200]
        risk = str(step.get("risk") or "unknown")[:50]
        decision = str(step.get("decision") or "unknown")[:100]
        success = step.get("success")
        evidence.append(
            f"step[{index}]: tool={tool}; risk={risk}; decision={decision}; success={success!r}"
        )

    summary = (
        f"Scrappy OS task {remote_id} reached terminal outcome {outcome}; "
        f"{len(verified_steps)} verified tool step(s), {successes} succeeded and {failures} failed."
    )
    experience = Experience(
        objective_id=remote_id,
        summary=summary,
        outcome=outcome,
        evidence=evidence,
        # Lessons are intentionally empty here. A later reflection process may
        # derive lessons, but transport evidence must not masquerade as insight.
        lessons=[],
    )
    recorded = envelope(
        actor_id="service:vault-zeta",
        actor_kind=ActorKind.SERVICE,
        event_type=EventType.EXPERIENCE_RECORDED,
        source="vault-zeta",
        payload=experience,
        correlation_id=expected_correlation_id,
        provenance=[
            ProvenanceRef(
                source="scrappy-os",
                observed_at=datetime.now(timezone.utc),
                reference=f"/tasks/{remote_id}",
            )
        ],
    )
    return DistilledExperience(
        correlation_id=expected_correlation_id,
        remote_objective_id=remote_id,
        experience=experience,
        envelope=recorded,
    )


class SyncbondExperienceStore:
    """PostgreSQL store with delivery-idempotent continuity writes."""

    async def put(
        self,
        item: DistilledExperience,
        *,
        user_id: UUID | None = None,
    ) -> tuple[dict[str, Any], bool]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO syncbond_experiences
                    (correlation_id, remote_objective_id, user_id, outcome, summary,
                     evidence, lessons, source, envelope)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, 'scrappy-os', $8::jsonb)
                ON CONFLICT (correlation_id, remote_objective_id) DO NOTHING
                RETURNING id, correlation_id, remote_objective_id, user_id, outcome,
                          summary, evidence, lessons, source, envelope, created_at
                """,
                item.correlation_id,
                item.remote_objective_id,
                user_id,
                item.experience.outcome,
                item.experience.summary,
                json.dumps(item.experience.evidence),
                json.dumps(item.experience.lessons),
                json.dumps(item.envelope.model_dump(mode="json"), default=str),
            )
            created = row is not None
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT id, correlation_id, remote_objective_id, user_id, outcome,
                           summary, evidence, lessons, source, envelope, created_at
                    FROM syncbond_experiences
                    WHERE correlation_id = $1 AND remote_objective_id = $2
                    """,
                    item.correlation_id,
                    item.remote_objective_id,
                )
            if row is None:  # pragma: no cover - defensive database invariant
                raise RuntimeError("experience insert conflicted but existing row was not found")
            return dict(row), created

    async def get(self, correlation_id: UUID, remote_objective_id: UUID) -> dict[str, Any] | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, correlation_id, remote_objective_id, user_id, outcome,
                       summary, evidence, lessons, source, envelope, created_at
                FROM syncbond_experiences
                WHERE correlation_id = $1 AND remote_objective_id = $2
                """,
                correlation_id,
                remote_objective_id,
            )
            return dict(row) if row is not None else None
