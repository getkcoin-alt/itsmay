"""Durable objective and initiative primitives for Scrappy.

Goals belong to the operator/continuity plane. Scrappy may infer the next task,
but a proposed task is state, not authority: it cannot grant tool permissions or
execute itself.
"""

from .models import Goal, GoalProvenance, GoalStatus, GoalTask, TaskProvenance, TaskState
from .planner import NextStepPlanner, PlannedStep
from .runtime import InitiativeRuntime
from .store import GoalStore, PostgresGoalStore, SqliteGoalStore

__all__ = [
    "Goal",
    "GoalProvenance",
    "GoalStatus",
    "GoalTask",
    "GoalStore",
    "InitiativeRuntime",
    "NextStepPlanner",
    "PlannedStep",
    "PostgresGoalStore",
    "SqliteGoalStore",
    "TaskProvenance",
    "TaskState",
]
