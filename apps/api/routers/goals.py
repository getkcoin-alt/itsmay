"""Authenticated control surface for durable goals and bounded initiative."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.goals.models import GoalStatus, TaskState

router = APIRouter(prefix="/v1/goals", tags=["goals"])


class TaskStateBody(BaseModel):
    state: TaskState


class GoalStatusBody(BaseModel):
    status: GoalStatus


@router.get("")
async def goals(request: Request) -> dict:
    runtime = getattr(request.app.state, "initiative", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="initiative runtime unavailable")
    return await runtime.status()


@router.post("/plan")
async def plan_next(request: Request) -> dict:
    runtime = getattr(request.app.state, "initiative", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="initiative runtime unavailable")
    try:
        task = await runtime.tick()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"planned": task is not None, "task": task.to_dict() if task else None}


@router.post("/tasks/{task_id}/state")
async def update_task(task_id: str, body: TaskStateBody, request: Request) -> dict:
    runtime = getattr(request.app.state, "initiative", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="initiative runtime unavailable")
    try:
        task = await runtime.manager.set_task_state(task_id, body.state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task": task.to_dict()}


@router.post("/{goal_id}/status")
async def update_goal(goal_id: str, body: GoalStatusBody, request: Request) -> dict:
    runtime = getattr(request.app.state, "initiative", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="initiative runtime unavailable")
    try:
        goal = await runtime.manager.set_goal_status(goal_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"goal": goal.to_dict()}
