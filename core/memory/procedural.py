"""Workflow miner — distill recurring tool-call traces into procedural memories.

Scrappy's third memory type. The chat router records a compact trace per
tool-using turn (`role="tool"` message: `{"goal", "steps"}`). `mine_workflows`
reads recent traces, asks the LLM to name the *recurring* multi-step patterns,
and saves each as a `kind="procedural"` semantic memory — a **playbook**. Those
playbooks are later retrieved by goal-similarity and injected into the prompt so
Scrappy follows its own proven procedure instead of re-reasoning from scratch.

Sibling to `consolidator.py` (facts); runs in the same nightly `/v1/memory/
consolidate` pass. Reads traces through the episodic-store abstraction, so it
works on both the Postgres and the sovereign SQLite backends. Idempotent on
exact playbook content, like the consolidator.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol
from uuid import UUID

from core.brain.llm import LLMClient, Message
from core.logging import get_logger

log = get_logger(__name__)

# Don't bother the LLM until there's enough signal to find a *recurring* pattern.
_MIN_TRACES = 3
_MAX_TRACES = 80


class _TraceSource(Protocol):
    async def recent_tool_traces(self, user_id: UUID, *, limit: int = ...) -> list[str]: ...


class _MemorySink(Protocol):
    async def content_exists(self, user_id: UUID, content: str) -> bool: ...
    async def write(
        self, user_id: UUID, kind: Any, content: str, embedding: Any,
        *, source: str | None = ..., importance: float = ...,
    ) -> UUID: ...


class _Embedder(Protocol):
    async def embed(self, text: str) -> Any: ...


_MINE_PROMPT = """\
Below are traces of tool-using turns — the user's goal and the ordered sequence
of tools the assistant ran to satisfy it. Identify RECURRING multi-step
workflows (patterns that appear across multiple traces) and turn each into a
reusable playbook.

Rules:
- Only extract a workflow that recurs (two or more similar traces) OR is a clear,
  reusable multi-step procedure. Skip one-off, single-tool actions.
- `name`: a short imperative title (e.g. "Email the weekly payout summary").
- `trigger`: a short phrase describing WHEN to use it — phrase it like the user's
  request, so it matches future goals.
- `steps`: the ordered tool sequence, each "tool.name — why", 2–6 steps.
- `importance`: 0.6–0.8.

Respond with ONLY a JSON array (no markdown fences, no extra text):
[{"name": "...", "trigger": "...", "steps": ["tool.a — why", "tool.b — why"], "importance": 0.7}]
If there is no reusable pattern, respond with [].

TRACES:
{traces}
"""


def _parse_traces(raw: list[str]) -> list[dict]:
    out: list[dict] = []
    for r in raw:
        try:
            t = json.loads(r)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(t, dict) and t.get("steps"):
            out.append(t)
    return out


def _digest(traces: list[dict]) -> str:
    """One line per trace: goal + tool sequence — compact input for the miner."""
    lines: list[str] = []
    for t in traces[:_MAX_TRACES]:
        goal = str(t.get("goal", ""))[:160]
        seq = " → ".join(
            f"{s.get('tool', '?')}{'' if s.get('ok', True) else '(blocked)'}"
            for s in t.get("steps", [])
            if isinstance(s, dict)
        )
        lines.append(f"- goal: {goal} | tools: {seq}")
    return "\n".join(lines)[:6000]


def format_playbook(name: str, trigger: str, steps: list[str]) -> str:
    """The stored, self-contained playbook text (also what gets embedded/retrieved)."""
    lines = [f"PLAYBOOK: {name}"]
    if trigger:
        lines.append(f"When: {trigger}")
    lines.append("Steps:")
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    return "\n".join(lines)


async def mine_workflows(
    llm: LLMClient,
    semantic: _MemorySink,
    embedder: _Embedder,
    episodic: _TraceSource,
    user_id: UUID,
) -> dict[str, int]:
    """Extract recurring workflows from recent traces → procedural memories.

    Returns {"traces": N, "playbooks": N, "saved": N, "skipped": N}.
    """
    empty = {"traces": 0, "playbooks": 0, "saved": 0, "skipped": 0}
    traces = _parse_traces(await episodic.recent_tool_traces(user_id, limit=200))
    if len(traces) < _MIN_TRACES:
        log.info("mine.not_enough_traces", count=len(traces))
        return {**empty, "traces": len(traces)}

    # .replace (not .format): the prompt contains literal JSON braces.
    prompt = _MINE_PROMPT.replace("{traces}", _digest(traces))
    messages = [
        Message(role="system", content="You extract reusable workflow playbooks."),
        Message(role="user", content=prompt),
    ]
    full: list[str] = []
    async for chunk in llm.chat_stream(messages, temperature=0.2):
        if chunk.delta:
            full.append(chunk.delta)

    raw = "".join(full).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        playbooks: list[dict] = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("mine.parse_error", err=str(e), raw=raw[:200])
        return {**empty, "traces": len(traces)}

    saved = 0
    skipped = 0
    for pb in playbooks:
        name = str(pb.get("name", "")).strip()
        trigger = str(pb.get("trigger", "")).strip()
        steps = [str(s).strip() for s in (pb.get("steps") or []) if str(s).strip()]
        if not name or not steps:
            skipped += 1
            continue
        content = format_playbook(name, trigger, steps)
        if await semantic.content_exists(user_id, content):
            skipped += 1
            continue
        try:
            # Embed the trigger (goal-like) so retrieval matches future requests.
            embedding = await embedder.embed(trigger or name)
            importance = min(0.9, max(0.5, float(pb.get("importance", 0.7))))
            await semantic.write(
                user_id,
                "procedural",
                content,
                embedding,
                source="procedural-miner",
                importance=importance,
            )
            saved += 1
        except Exception as e:
            log.warning("mine.save_error", err=str(e), name=name[:80])
            skipped += 1

    log.info(
        "mine.done",
        traces=len(traces),
        playbooks=len(playbooks),
        saved=saved,
        skipped=skipped,
    )
    return {"traces": len(traces), "playbooks": len(playbooks), "saved": saved, "skipped": skipped}
