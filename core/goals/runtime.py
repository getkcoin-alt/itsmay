"""Continuous initiative loop: decide one next TODO, then wait.

This is intentionally not an autonomous executor. The loop may create a bounded
planning task from an operator-owned goal. Existing policy/approval and tool
execution layers remain the only path to consequential side effects.
"""

from __future__ import annotations

import asyncio

from core.goals.manager import GoalManager
from core.goals.models import GoalProvenance, GoalStatus, GoalTask
from core.goals.planner import NextStepPlanner
from core.logging import get_logger
from core.presence.models import PresenceEvent
from core.presence.runtime import PresenceRuntime

log = get_logger(__name__)


class InitiativeRuntime:
    def __init__(
        self,
        manager: GoalManager,
        planner: NextStepPlanner,
        *,
        mission_statement: str,
        presence: PresenceRuntime | None = None,
        interval_seconds: float = 90.0,
        enabled: bool = True,
    ) -> None:
        if interval_seconds < 10:
            raise ValueError("initiative interval must be at least 10 seconds")
        self.manager = manager
        self.planner = planner
        self.mission_statement = (mission_statement or "").strip()
        self.presence = presence
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._runner: asyncio.Task | None = None
        self._tick_lock = asyncio.Lock()
        self.last_task: GoalTask | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._runner and not self._runner.done())

    async def start(self) -> None:
        if not self.enabled or self.running:
            return
        if not self.mission_statement:
            self.last_error = "MISSION_STATEMENT is empty"
            return
        await self.manager.ensure_primary(
            self.mission_statement,
            provenance=GoalProvenance.OPERATOR_CONFIG,
            source_ref="MISSION_STATEMENT",
        )
        self._runner = asyncio.create_task(
            self._loop(),
            name="scrappy-initiative-loop",
        )
        log.info("initiative.started", interval_seconds=self.interval_seconds)

    async def stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
        self._runner = None
        log.info("initiative.stopped")

    async def _loop(self) -> None:
        try:
            # Let startup finish before using an upstream model.
            await asyncio.sleep(min(5.0, self.interval_seconds))
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    log.warning("initiative.tick_failed", error=self.last_error)
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            raise

    async def tick(self) -> GoalTask | None:
        """Choose one next TODO only when no unresolved work already exists."""
        if not self.enabled:
            return None
        async with self._tick_lock:
            goal = await self.manager.active_goal()
            if goal is None:
                goal = await self.manager.ensure_primary(
                    self.mission_statement,
                    provenance=GoalProvenance.OPERATOR_CONFIG,
                    source_ref="MISSION_STATEMENT",
                )
            if goal.status is not GoalStatus.ACTIVE:
                return None
            task = await self.planner.plan_once(goal)
            if task is None:
                return None
            self.last_task = task
            self.last_error = None
            log.info(
                "initiative.next_task",
                goal_id=goal.id,
                task_id=task.id,
                title=task.title,
                provenance=task.provenance.value,
            )
            if self.presence is not None:
                await self.presence.submit(
                    PresenceEvent(
                        type="goal.next_task",
                        source="initiative",
                        domain="mission",
                        importance=0.9,
                        urgency=0.65,
                        confidence=0.9,
                        novelty=0.9,
                        goal_relevance=1.0,
                        interruption_cost=0.25,
                        payload={
                            "message": f"Next step: {task.title}",
                            "goal_id": goal.id,
                            "task_id": task.id,
                            "provenance": task.provenance.value,
                        },
                    )
                )
            return task

    async def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "interval_seconds": self.interval_seconds,
            "last_task": self.last_task.to_dict() if self.last_task else None,
            "last_error": self.last_error,
            "ledger": await self.manager.snapshot(),
            "authority": "plan_only_policy_gated_execution",
        }
