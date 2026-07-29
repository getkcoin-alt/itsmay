"""Worker endpoints — the local Mac worker's link to the server.

`scrappy worker` (apps/cli) long-polls `/v1/worker/next` for commands a terminal
agent wants run on Karnveer's Mac, executes them locally, and posts the output
back to `/v1/worker/result`. A lightweight `/v1/worker/heartbeat` keeps the
worker marked online while a long command is running so concurrent agent
commands still route to the Mac instead of falling back to the server.

All of these sit behind the bearer-auth middleware, so the worker authenticates
with the same VAULT_API_KEY as every other client.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel

from core.worker.bridge import get_worker_bridge

router = APIRouter(prefix="/v1/worker", tags=["worker"])


class ResultBody(BaseModel):
    command_id: str
    output: str


class ProgressBody(BaseModel):
    command_id: str
    chunk: str


@router.get("/next", response_model=None)
async def next_command() -> Response | dict:
    """Long-poll for the next command. 204 if none arrives within the window."""
    command = await get_worker_bridge().next_command(wait=25.0)
    if command is None:
        return Response(status_code=204)
    return command.to_payload()


@router.post("/result")
async def post_result(body: ResultBody) -> dict:
    """Hand back the output of a command the worker finished running."""
    ok = get_worker_bridge().complete(body.command_id, body.output)
    return {"ok": ok}


@router.post("/progress")
async def post_progress(body: ProgressBody) -> dict:
    """Relay one live progress milestone from a still-running command to its
    server-side sink, so the user hears what Claude Code is doing as it happens.
    Also counts as presence — a streaming build keeps the worker marked online."""
    bridge = get_worker_bridge()
    bridge.touch()
    ok = bridge.progress(body.command_id, body.chunk)
    return {"ok": ok}


@router.post("/heartbeat")
async def heartbeat() -> dict:
    """Keep the worker marked online during long-running commands."""
    get_worker_bridge().touch()
    return {"ok": True}


@router.get("/status")
async def status() -> dict:
    bridge = get_worker_bridge()
    return {
        "online": bridge.worker_online(),
        "seconds_since_seen": bridge.seconds_since_seen(),
    }
