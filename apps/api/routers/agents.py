"""REST endpoints for the Agents tab in the web console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.terminal_agent.registry import get_agent_store

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.get("")
async def list_agents() -> list[dict]:
    """List all terminal agents, newest first."""
    return [a.to_dict() for a in get_agent_store().list_all()]


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
