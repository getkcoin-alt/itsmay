"""REST endpoints for the Agents tab in the web console."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.config import get_settings
from core.terminal_agent.registry import get_agent_store

router = APIRouter(prefix="/v1/agents", tags=["agents"])


class SpawnRequest(BaseModel):
    task: str


async def _spawn_with_memory(request: Request, task: str):
    """Spawn an agent wired to semantic memory (RAG) for the configured user."""
    state = request.app.state
    user_uuid = None
    try:
        user_uuid = await state.episodic.get_or_create_user(get_settings().user_handle)
    except Exception:
        pass
    return get_agent_store().spawn(
        task,
        state.llm,
        embedder=getattr(state, "embedder", None),
        semantic=getattr(state, "semantic", None),
        user_uuid=user_uuid,
    )


@router.get("")
async def list_agents() -> list[dict]:
    """List all terminal agents, newest first."""
    return [a.to_dict() for a in get_agent_store().list_all()]


@router.post("/stream")
async def stream_agent(req: SpawnRequest, request: Request) -> EventSourceResponse:
    """Spawn a terminal agent and stream its log events as SSE in real time."""
    task = req.task.strip()
    if not task:
        raise HTTPException(status_code=422, detail="task is required")
    agent = await _spawn_with_memory(request, task)

    async def generate():
        yield {
            "event": "agent_started",
            "data": json.dumps({"agent_id": agent.id, "task": agent.task}),
        }
        last = 0
        while agent.status in ("pending", "running"):
            await asyncio.sleep(0.25)
            for entry in agent.log[last:]:
                yield {
                    "event": entry.kind,
                    "data": json.dumps({"text": entry.text, "ts": entry.ts}),
                }
            last = len(agent.log)
        # drain any entries written after the status flipped
        for entry in agent.log[last:]:
            yield {
                "event": entry.kind,
                "data": json.dumps({"text": entry.text, "ts": entry.ts}),
            }
        yield {
            "event": "agent_done",
            "data": json.dumps({"status": agent.status, "result": agent.result}),
        }

    return EventSourceResponse(generate())


@router.get("/{agent_id}/stream")
async def tail_agent(agent_id: str) -> EventSourceResponse:
    """Stream log events for an existing agent (replay history then follow live)."""
    agent = get_agent_store().get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")

    async def generate():
        # Replay existing entries first
        for entry in agent.log:
            yield {
                "event": entry.kind,
                "data": json.dumps({"text": entry.text, "ts": entry.ts}),
            }
        last = len(agent.log)
        # Follow live if still running
        while agent.status in ("pending", "running"):
            await asyncio.sleep(0.25)
            for entry in agent.log[last:]:
                yield {
                    "event": entry.kind,
                    "data": json.dumps({"text": entry.text, "ts": entry.ts}),
                }
            last = len(agent.log)
        for entry in agent.log[last:]:
            yield {
                "event": entry.kind,
                "data": json.dumps({"text": entry.text, "ts": entry.ts}),
            }
        yield {
            "event": "agent_done",
            "data": json.dumps({"status": agent.status, "result": agent.result}),
        }

    return EventSourceResponse(generate())


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    """Full detail (including log) for a specific terminal agent."""
    agent = get_agent_store().get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
    return agent.to_dict(full=True)


@router.delete("/{agent_id}")
async def cancel_agent(agent_id: str) -> dict:
    """Cancel a running terminal agent."""
    agent = get_agent_store().get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
    cancelled = agent.cancel()
    return {"cancelled": cancelled, "status": agent.status}
