"""Typed durable goal/task contracts.

The distinction between goal provenance and task provenance is deliberate:
operator intent remains distinguishable from a next step inferred by Scrappy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GoalProvenance(StrEnum):
    OPERATOR = "operator"
    OPERATOR_CONFIG = "operator_config"
    SCRAPPY_INFERRED = "scrappy_inferred"


class TaskProvenance(StrEnum):
    OPERATOR = "operator"
    SCRAPPY_PLANNER = "scrappy_planner"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskState(StrEnum):
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    objective: str
    provenance: GoalProvenance
    source_ref: str | None
    status: GoalStatus
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "provenance": self.provenance.value,
            "source_ref": self.source_ref,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class GoalTask:
    id: str
    goal_id: str
    title: str
    rationale: str
    success_criteria: tuple[str, ...]
    dependencies: tuple[str, ...]
    provenance: TaskProvenance
    source_ref: str | None
    state: TaskState
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "rationale": self.rationale,
            "success_criteria": list(self.success_criteria),
            "dependencies": list(self.dependencies),
            "provenance": self.provenance.value,
            "source_ref": self.source_ref,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
