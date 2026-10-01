"""Bounded one-step planner for Scrappy initiative.

The planner chooses one next TODO from an operator-owned objective. It cannot run
tools, grant permissions, create new goals, or mark its own work complete.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.brain.llm import LLMClient, Message
from core.goals.manager import GoalManager
from core.goals.models import Goal, GoalStatus, GoalTask, TaskProvenance


@dataclass(frozen=True, slots=True)
class PlannedStep:
    title: str
    rationale: str
    success_criteria: tuple[str, ...]


Proposer = Callable[[Goal, tuple[GoalTask, ...]], Awaitable[PlannedStep]]


def _extract_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3:
            raw = "\n".join(lines[1:-1]).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("planner returned no JSON object")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("planner output must be a JSON object")
    return value


def _validated_step(value: dict[str, Any]) -> PlannedStep:
    title = str(value.get("title") or "").strip()
    rationale = str(value.get("rationale") or "").strip()
    raw_criteria = value.get("success_criteria")
    if not isinstance(raw_criteria, list):
        raise ValueError("planner success_criteria must be a list")
    criteria = tuple(str(item).strip() for item in raw_criteria if str(item).strip())
    if not 1 <= len(title) <= 240:
        raise ValueError("planner task title is invalid")
    if len(rationale) > 2000:
        raise ValueError("planner rationale is too long")
    if not 1 <= len(criteria) <= 6 or any(len(item) > 400 for item in criteria):
        raise ValueError("planner success criteria are invalid")
    return PlannedStep(title, rationale, criteria)


class LLMNextStepProposer:
    """Provider-neutral adapter around the existing replaceable LLM cortex."""

    SYSTEM = (
        "You plan exactly ONE bounded next task for Scrappy.\n"
        "The objective is operator-owned. You may choose the next TODO, but you have no "
        "execution authority. Do not claim work happened. Do not request secrets. Do not "
        "change the objective. Prefer the smallest high-leverage step that advances it and "
        "has objectively checkable completion criteria.\n\n"
        "Return JSON only with keys: title, rationale, success_criteria."
    )

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def __call__(self, goal: Goal, history: tuple[GoalTask, ...]) -> PlannedStep:
        completed = [
            {
                "title": task.title,
                "state": task.state.value,
                "success_criteria": list(task.success_criteria),
            }
            for task in history[-12:]
        ]
        prompt = json.dumps(
            {
                "objective": goal.objective,
                "objective_provenance": goal.provenance.value,
                "previous_tasks": completed,
                "instruction": "Choose one next bounded TODO. Do not execute it.",
            },
            ensure_ascii=False,
        )
        pieces: list[str] = []
        async for chunk in self.llm.chat_stream(
            [
                Message(role="system", content=self.SYSTEM),
                Message(role="user", content=prompt),
            ],
            temperature=0.2,
            tools=None,
        ):
            if chunk.delta:
                pieces.append(chunk.delta)
        return _validated_step(_extract_object("".join(pieces)))


class NextStepPlanner:
    def __init__(
        self,
        manager: GoalManager,
        proposer: Proposer,
        *,
        source_ref: str = "initiative.next_step",
    ) -> None:
        self.manager = manager
        self.proposer = proposer
        self.source_ref = source_ref

    async def plan_once(self, goal: Goal) -> GoalTask | None:
        if goal.status is not GoalStatus.ACTIVE:
            return None
        if await self.manager.has_unresolved_work(goal.id):
            return None

        latest = next(
            (item for item in await self.manager.list_goals() if item.id == goal.id),
            goal,
        )
        step = await self.proposer(latest, latest.tasks)
        return await self.manager.add_task(
            goal.id,
            step.title,
            rationale=step.rationale,
            success_criteria=step.success_criteria,
            provenance=TaskProvenance.SCRAPPY_PLANNER,
            source_ref=self.source_ref,
        )
