"""Continuous Presence API.

These endpoints expose only real runtime state and normalized event intake. They
are protected by the existing /v1 bearer middleware. V1 makes attention
decisions (SILENCE/SPEAK) but deliberately performs no external side effects.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.presence.models import PresenceEvent
from core.presence.runtime import get_presence_runtime

router = APIRouter(prefix="/v1/presence", tags=["presence"])


@router.get("/status")
async def status() -> dict:
    return get_presence_runtime().status()


@router.get("/events/recent")
async def recent_events() -> dict:
    runtime = get_presence_runtime()
    return {"decisions": runtime.recent()}


@router.post("/events", status_code=202)
async def submit_event(event: PresenceEvent) -> dict:
    runtime = get_presence_runtime()
    if not runtime.running:
        raise HTTPException(status_code=503, detail="presence runtime is not running")
    accepted = await runtime.submit(event)
    return {
        "accepted": accepted,
        "duplicate": not accepted,
        "event_id": event.id,
        "trace_id": event.trace_id,
    }
