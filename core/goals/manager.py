"""Controller-owned goal state transitions.

The model may propose a next task. Only this manager can persist it, and it never
turns a task into a tool call. Execution remains behind the existing approval
boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from core.goals.models import (
    TERMINAL_TASK_STATES,
    Goal,
    GoalProvenance,
    GoalState,
    GoalStatus,
    GoalTask,
    TaskProvenance,
    TaskState,
)
from core.goals.store import GoalStore


_ALLOWED_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.READY: {
        TaskState.ACTIVE,
        TaskState.BLOCKED,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.ACTIVE: {
        TaskState.READY,
        TaskState.BLOCKED,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {TaskState.READY, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.FAILED: {TaskState.READY, TaskState.CANCELLED},
    TaskState.COMPLETED: set(),
    TaskState.CANCELLED: set(),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_objective(value: str) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= 4000:
        raise ValueError("goal objective must contain 1 to 4000 characters")
    return value


def _clean_task(
    title: str,
    rationale: str,
    success_criteria: Sequence[str],
) -> tuple[str, str, tuple[str, ...]]:
    title = (title or "").strip()
    rationale = (rationale or "").strip()
    criteria = tuple(str(item).strip() for item in success_criteria if str(item).strip())
    if not 1 <= len(title) <= 240:
        raise ValueError("task title must contain 1 to 240 characters")
    if len(rationale) > 2000:
        raise ValueError("task rationale exceeds 2000 characters")
    if not 1 <= len(criteria) <= 6 or any(len(item) > 400 for item in criteria):
        raise ValueError("task requires 1 to 6 bounded success criteria")
    return title, rationale, criteria


class GoalManager:
    def __init__(self, store: GoalStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()

    async def ensure_primary(
        self,
        objective: str,
        *,
        provenance: GoalProvenance = GoalProvenance.OPERATOR_CONFIG,
        source_ref: str | None = "MISSION_STATEMENT",
    ) -> Goal:
        objective = _clean_objective(objective)
        async with self._lock:
            state = await self.store.load()
            current = next(
                (
                    goal
                    for goal in state.goals
                    if goal.status in {GoalStatus.ACTIVE, GoalStatus.PAUSED}
                ),
                None,
            )
            if current is not None:
                return current
            now = _now()
            goal = Goal(
                id=str(uuid4()),
                objective=objective,
                provenance=provenance,
                source_ref=source_ref,
                status=GoalStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            await self.store.save(replace(state, goals=(*state.goals, goal)))
            return goal

    async def active_goal(self) -> Goal | None:
        state = await self.store.load()
        return next(
            (
                goal
                for goal in state.goals
                if goal.status in {GoalStatus.ACTIVE, GoalStatus.PAUSED}
            ),
            None,
        )

    async def list_goals(self) -> list[Goal]:
        return list((await self.store.load()).goals)

    async def add_task(
        self,
        goal_id: str,
        title: str,
        *,
        rationale: str,
        success_criteria: Sequence[str],
        provenance: TaskProvenance,
        source_ref: str | None = None,
    ) -> GoalTask:
        title, rationale, criteria = _clean_task(title, rationale, success_criteria)
        async with self._lock:
            state = await self.store.load()
            goal = next((item for item in state.goals if item.id == goal_id), None)
            if goal is None:
                raise ValueError("unknown goal")
            if goal.status is not GoalStatus.ACTIVE:
                raise ValueError("goal is not active")
            if any(task.state not in TERMINAL_TASK_STATES for task in goal.tasks):
                raise ValueError("goal already has unresolved work")
            now = _now()
            task = GoalTask(
                id=str(uuid4()),
                title=title,
                rationale=rationale,
                success_criteria=criteria,
                provenance=provenance,
                source_ref=source_ref,
                state=TaskState.READY,
                created_at=now,
                updated_at=now,
            )
            updated_goal = replace(goal, tasks=(*goal.tasks, task), updated_at=now)
            goals = tuple(updated_goal if item.id == goal.id else item for item in state.goals)
            await self.store.save(replace(state, goals=goals))
            return task

    async def set_task_state(self, task_id: str, state_value: TaskState) -> GoalTask:
        async with self._lock:
            state = await self.store.load()
            for goal in state.goals:
                task = next((item for item in goal.tasks if item.id == task_id), None)
                if task is None:
                    continue
                if state_value is not task.state and state_value not in _ALLOWED_TASK_TRANSITIONS[task.state]:
                    raise ValueError(
                        f"invalid task transition {task.state.value}->{state_value.value}"
                    )
                now = _now()
                updated_task = replace(
                    task,
                    state=state_value,
                    updated_at=now,
                    completed_at=now if state_value is TaskState.COMPLETED else task.completed_at,
                )
                tasks = tuple(updated_task if item.id == task.id else item for item in goal.tasks)
                updated_goal = replace(goal, tasks=tasks, updated_at=now)
                goals = tuple(updated_goal if item.id == goal.id else item for item in state.goals)
                await self.store.save(replace(state, goals=goals))
                return updated_task
            raise ValueError("unknown task")

    async def set_goal_status(self, goal_id: str, status: GoalStatus) -> Goal:
        async with self._lock:
            state = await self.store.load()
            goal = next((item for item in state.goals if item.id == goal_id), None)
            if goal is None:
                raise ValueError("unknown goal")
            if goal.status in {GoalStatus.COMPLETED, GoalStatus.CANCELLED} and status is not goal.status:
                raise ValueError("terminal goal cannot be resumed")
            updated = replace(goal, status=status, updated_at=_now())
            goals = tuple(updated if item.id == goal.id else item for item in state.goals)
            await self.store.save(replace(state, goals=goals))
            return updated

    async def next_ready_task(self, goal_id: str) -> GoalTask | None:
        state = await self.store.load()
        goal = next((item for item in state.goals if item.id == goal_id), None)
        if goal is None or goal.status is not GoalStatus.ACTIVE:
            return None
        return next((task for task in goal.tasks if task.state is TaskState.READY), None)

    async def has_unresolved_work(self, goal_id: str) -> bool:
        state = await self.store.load()
        goal = next((item for item in state.goals if item.id == goal_id), None)
        return bool(goal and any(task.state not in TERMINAL_TASK_STATES for task in goal.tasks))

    async def snapshot(self) -> dict:
        state = await self.store.load()
        active = next(
            (
                goal
                for goal in state.goals
                if goal.status in {GoalStatus.ACTIVE, GoalStatus.PAUSED}
            ),
            None,
        )
        return {
            "schema_version": state.schema_version,
            "active_goal_id": active.id if active else None,
            "goals": [goal.to_dict() for goal in state.goals],
        }
