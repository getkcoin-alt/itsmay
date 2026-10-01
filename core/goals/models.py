"""Typed durable goal/task contracts.

Operator intent remains distinguishable from a next step inferred by Scrappy.
Planning state never carries tool authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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


def _dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class GoalTask:
    id: str
    title: str
    rationale: str
    success_criteria: tuple[str, ...]
    provenance: TaskProvenance
    source_ref: str | None
    state: TaskState
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "rationale": self.rationale,
            "success_criteria": list(self.success_criteria),
            "provenance": self.provenance.value,
            "source_ref": self.source_ref,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "GoalTask":
        return cls(
            id=str(value["id"]),
            title=str(value["title"]),
            rationale=str(value.get("rationale") or ""),
            success_criteria=tuple(str(x) for x in value.get("success_criteria", ())),
            provenance=TaskProvenance(value["provenance"]),
            source_ref=value.get("source_ref"),
            state=TaskState(value["state"]),
            created_at=_dt(value["created_at"]) or datetime.now(UTC),
            updated_at=_dt(value["updated_at"]) or datetime.now(UTC),
            completed_at=_dt(value.get("completed_at")),
        )


@dataclass(frozen=True, slots=True)
class Goal:
    id: str
    objective: str
    provenance: GoalProvenance
    source_ref: str | None
    status: GoalStatus
    created_at: datetime
    updated_at: datetime
    tasks: tuple[GoalTask, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "provenance": self.provenance.value,
            "source_ref": self.source_ref,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Goal":
        return cls(
            id=str(value["id"]),
            objective=str(value["objective"]),
            provenance=GoalProvenance(value["provenance"]),
            source_ref=value.get("source_ref"),
            status=GoalStatus(value["status"]),
            created_at=_dt(value["created_at"]) or datetime.now(UTC),
            updated_at=_dt(value["updated_at"]) or datetime.now(UTC),
            tasks=tuple(GoalTask.from_dict(x) for x in value.get("tasks", ())),
        )


@dataclass(frozen=True, slots=True)
class GoalState:
    schema_version: int = 1
    goals: tuple[Goal, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "goals": [goal.to_dict() for goal in self.goals],
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "GoalState":
        if not value:
            return cls()
        if int(value.get("schema_version", 0)) != 1:
            raise ValueError("unsupported goal-state schema")
        return cls(
            schema_version=1,
            goals=tuple(Goal.from_dict(x) for x in value.get("goals", ())),
        )
