"""Parse Claude Code `--output-format stream-json` events into speakable milestones.

Headless `claude -p` normally prints nothing until it's completely done, so a
multi-minute build is a silent block. `--output-format stream-json --verbose`
instead emits one JSON event per line as Claude works. This module turns that
firehose into a handful of short, human milestones ("Writing index.html",
"Running: npm install", plus Claude's own thinking) that Scrappy narrates live —
so Boss hears what's happening and the process, instead of dead air.

Pure and dependency-free: the Mac worker feeds it stdout lines one at a time and
POSTs the milestones back over the progress channel. It also pulls the final
assistant text out of the terminal `result` event — that's the text carrying the
`SCRAPPY_RESULT:` line coder.build parses, which stream-json JSON-wraps.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Tools worth saying out loud during a build. Read/Glob/Grep/LS/TodoWrite and the
# like are deliberately skipped — too granular for a spoken play-by-play.
_TOOL_VERB = {
    "Write": "Writing",
    "Edit": "Editing",
    "MultiEdit": "Editing",
    "NotebookEdit": "Editing",
}
_MAX_MILESTONE = 160


@dataclass(slots=True)
class ParsedEvent:
    """One stream-json line, distilled. `milestones` are spoken as they arrive;
    `final_text` is set only by the terminal result event (the assistant's last
    message, which carries the SCRAPPY_RESULT line)."""

    milestones: list[str] = field(default_factory=list)
    final_text: str | None = None


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_MILESTONE else text[:_MAX_MILESTONE].rstrip() + "…"


def _strip_result_line(text: str) -> str:
    """Drop the machine SCRAPPY_RESULT line(s) so we never narrate raw JSON."""
    keep = [ln for ln in text.splitlines() if "SCRAPPY_RESULT" not in ln]
    return "\n".join(keep).strip()


def _tool_milestone(name: str, inp: dict) -> str | None:
    """A short line for a tool_use block, or None to skip (low-signal tools)."""
    if name in _TOOL_VERB:
        target = inp.get("file_path") or inp.get("notebook_path") or ""
        verb = _TOOL_VERB[name]
        return f"{verb} {_basename(str(target))}" if target else verb
    if name == "Bash":
        desc = str(inp.get("description") or "").strip()
        if desc:
            return _clip(desc)
        cmd = " ".join(str(inp.get("command") or "").split())
        return f"Running: {_clip(cmd)}" if cmd else "Running a command"
    if name in ("WebFetch", "WebSearch"):
        q = str(inp.get("url") or inp.get("query") or "").strip()
        return f"Looking up {_clip(q)}" if q else "Looking something up"
    if name == "Task":
        return "Spinning up a sub-agent"
    return None


def parse_stream_line(line: str) -> ParsedEvent:
    """Distill one NDJSON line from `claude --output-format stream-json`.

    Non-JSON or uninteresting lines yield an empty ParsedEvent (nothing to say).
    An `assistant` event yields a milestone per text block (Claude's thinking,
    minus the SCRAPPY_RESULT line) and per notable tool_use block. The `result`
    event yields `final_text` — the assistant's final message.
    """
    line = (line or "").strip()
    if not line:
        return ParsedEvent()
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ParsedEvent()
    if not isinstance(obj, dict):
        return ParsedEvent()

    etype = obj.get("type")
    if etype == "result":
        result = obj.get("result")
        return ParsedEvent(final_text=result if isinstance(result, str) else None)

    if etype == "assistant":
        message = obj.get("message") or {}
        content = message.get("content") or []
        if not isinstance(content, list):
            return ParsedEvent()
        milestones: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                spoken = _strip_result_line(str(block.get("text") or ""))
                if len(spoken) >= 2:
                    milestones.append(_clip(spoken))
            elif btype == "tool_use":
                m = _tool_milestone(str(block.get("name") or ""), block.get("input") or {})
                if m:
                    milestones.append(m)
        return ParsedEvent(milestones=milestones)

    return ParsedEvent()
