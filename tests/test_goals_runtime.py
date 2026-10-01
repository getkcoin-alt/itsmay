from __future__ import annotations

import pytest

from core.goals.manager import GoalManager
from core.goals.models import (
    GoalProvenance,
    GoalStatus,
    TaskProvenance,
    TaskState,
)
from core.goals.planner import NextStepPlanner, PlannedStep
from core.goals.runtime import InitiativeRuntime
from core.goals.store import SqliteGoalStore


async def _goal(manager: GoalManager):
    return await manager.ensure_primary(
        "Build the next verified Scrappy capability",
        provenance=GoalProvenance.OPERATOR,
        source_ref="test.operator",
    )


@pytest.mark.asyncio
async def test_goal_and_task_survive_store_reopen(tmp_path):
    path = str(tmp_path / "vault.db")
    first_store = SqliteGoalStore(path)
    first = GoalManager(first_store)
    goal = await _goal(first)
    task = await first.add_task(
        goal.id,
        "Prove the persistence seam",
        rationale="Continuity must survive a process restart.",
        success_criteria=("A fresh store can read the same task.",),
        provenance=TaskProvenance.OPERATOR,
        source_ref="test.operator",
    )
    first_store.close()

    second_store = SqliteGoalStore(path)
    second = GoalManager(second_store)
    restored = await second.active_goal()
    assert restored is not None
    assert restored.id == goal.id
    assert restored.provenance is GoalProvenance.OPERATOR
    assert restored.tasks[0].id == task.id
    assert restored.tasks[0].state is TaskState.READY
    second_store.close()


@pytest.mark.asyncio
async def test_planner_creates_one_next_task_then_waits(tmp_path):
    store = SqliteGoalStore(str(tmp_path / "vault.db"))
    manager = GoalManager(store)
    goal = await _goal(manager)
    calls = 0

    async def propose(current, history):
        nonlocal calls
        calls += 1
        assert current.id == goal.id
        return PlannedStep(
            "Run the next bounded verification",
            "Choose one checkable step rather than an open-ended project.",
            ("The verification has deterministic evidence.",),
        )

    planner = NextStepPlanner(manager, propose)
    first = await planner.plan_once(goal)
    second = await planner.plan_once(goal)
    assert first is not None
    assert first.provenance is TaskProvenance.SCRAPPY_PLANNER
    assert first.state is TaskState.READY
    assert second is None
    assert calls == 1
    store.close()


@pytest.mark.asyncio
async def test_completed_task_allows_next_decision(tmp_path):
    store = SqliteGoalStore(str(tmp_path / "vault.db"))
    manager = GoalManager(store)
    goal = await _goal(manager)
    serial = 0

    async def propose(current, history):
        nonlocal serial
        serial += 1
        return PlannedStep(
            f"Next verified step {serial}",
            "Advance only after the previous task reaches a terminal state.",
            ("One bounded result is recorded.",),
        )

    planner = NextStepPlanner(manager, propose)
    first = await planner.plan_once(goal)
    assert first is not None
    await manager.set_task_state(first.id, TaskState.COMPLETED)
    second = await planner.plan_once(goal)
    assert second is not None
    assert second.id != first.id
    assert serial == 2
    store.close()


@pytest.mark.asyncio
async def test_pause_is_durable_and_blocks_initiative(tmp_path):
    path = str(tmp_path / "vault.db")
    store = SqliteGoalStore(path)
    manager = GoalManager(store)
    goal = await _goal(manager)

    async def propose(current, history):
        raise AssertionError("paused goal must not invoke the planner")

    await manager.set_goal_status(goal.id, GoalStatus.PAUSED)
    planner = NextStepPlanner(manager, propose)
    assert await planner.plan_once(await manager.active_goal()) is None
    store.close()

    reopened = SqliteGoalStore(path)
    restored = await GoalManager(reopened).active_goal()
    assert restored is not None
    assert restored.status is GoalStatus.PAUSED
    reopened.close()


@pytest.mark.asyncio
async def test_initiative_tick_is_planning_only(tmp_path):
    store = SqliteGoalStore(str(tmp_path / "vault.db"))
    manager = GoalManager(store)

    async def propose(current, history):
        return PlannedStep(
            "Inspect the next production blocker",
            "Decide the next TODO without executing it.",
            ("A bounded blocker is named.",),
        )

    runtime = InitiativeRuntime(
        manager,
        NextStepPlanner(manager, propose),
        mission_statement="Ship verified Scrappy improvements",
        interval_seconds=60,
    )
    await manager.ensure_primary(
        runtime.mission_statement,
        provenance=GoalProvenance.OPERATOR_CONFIG,
        source_ref="MISSION_STATEMENT",
    )
    task = await runtime.tick()
    assert task is not None
    assert task.state is TaskState.READY
    status = await runtime.status()
    assert status["authority"] == "plan_only_policy_gated_execution"
    assert status["ledger"]["active_goal_id"]
    store.close()
