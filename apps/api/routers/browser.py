"""Browser watch event endpoint.

The Mac tab watcher POSTs here whenever a watched Chrome tab changes.
This endpoint runs LLM analysis and returns a short summary that the
Mac agent shows as a macOS notification.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from core.brain.llm import LLMClient, Message

router = APIRouter(prefix="/v1/browser", tags=["browser"])


class TabEvent(BaseModel):
    title: str = ""
    url: str = ""
    snapshot: str = Field(min_length=1)
    diff: str = ""


@router.post("/tab-event")
async def tab_event(body: TabEvent, request: Request) -> dict:
    """Receive a tab-content change from the Mac watcher, return an AI summary."""
    llm: LLMClient = request.app.state.llm
    summary = await _summarize(llm, body)
    return {"summary": summary, "url": body.url, "title": body.title}


async def _summarize(llm: LLMClient, event: TabEvent) -> str:
    has_diff = bool(event.diff.strip())
    diff_section = (
        f"Diff (+ added / - removed lines):\n{event.diff[:2000]}"
        if has_diff
        else "(no diff — full snapshot provided)"
    )
    prompt = (
        "A browser tab has just changed. "
        "In 2-3 sentences describe what changed and flag anything worth the user's attention. "
        "Be concrete — quote key numbers, names, or status words if they appear.\n\n"
        f"Tab: {event.title or event.url}\n"
        f"URL: {event.url}\n\n"
        f"{diff_section}\n\n"
        f"Full snapshot (first 3000 chars):\n{event.snapshot[:3000]}"
    )
    msgs = [Message(role="user", content=prompt)]
    parts: list[str] = []
    async for chunk in llm.chat_stream(msgs, temperature=0.3):
        if chunk.delta:
            parts.append(chunk.delta)
    return "".join(parts).strip()
