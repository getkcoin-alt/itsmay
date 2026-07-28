"""Voice narration + phrase streaming — what makes Scrappy feel live to talk to.

Two concerns, both pure (no audio deps, so they're testable off the Mac) and
shared by the Mac voice loop:

- **pop_phrase** (IM-4.1): chunk a growing token buffer into speakable phrases at
  natural breaks, so TTS on sentence 1 starts while the model is still writing
  sentence 2 — no walkie-talkie dead-air between generation and speech.
- **narrate_tool / narrate_approval** (IM-4.3): short spoken lines for tool
  activity, so a running tool isn't silent — "Checking my memory…", "Drafting a
  change to myself…", "I need your OK to send that." Self-modification (Epic 5)
  gets narrated out loud, so you hear what Scrappy is doing to himself.
"""

from __future__ import annotations

import re

# ── phrase streaming (IM-4.1) ─────────────────────────────────────────

# Natural break points to end a spoken phrase on. MIN_CHUNK_CHARS stops us
# synthesizing 1-2 word fragments (which sound clipped).
PHRASE_BREAK = re.compile(r"([.!?\n,;—–:])\s+")
MIN_CHUNK_CHARS = 18


def pop_phrase(buf: str) -> tuple[str | None, str]:
    """Pop the first phrase ending at a natural break once it's long enough.

    Returns (phrase, remainder), or (None, buf) if nothing is ready yet. Lets the
    caller ship sentence 1 to TTS while sentence 2 is still streaming in.
    """
    for m in PHRASE_BREAK.finditer(buf):
        end = m.end()
        if end >= MIN_CHUNK_CHARS:
            return buf[:end].strip(), buf[end:]
    return None, buf


# ── tool narration (IM-4.3) ───────────────────────────────────────────

TOOL_ACK: dict[str, str] = {
    "web": "Searching.",
    "web.search": "Searching.",
    "web.fetch": "Fetching that.",
    "memory": "Checking my memory.",
    "memory.search": "Checking my memory.",
    "memory.save": "Got it.",
    "terminal": "Running that now.",
    "terminal.spawn": "Spinning that up.",
    "coder": "Getting Claude on it.",
    "coder.code": "Getting Claude on it.",
    "gmail": "Checking your email.",
    "cal": "Checking your calendar.",
    "browser": "Opening the browser.",
    # expert delegations
    "ask_researcher": "Looking into it.",
    "ask_engineer": "On it.",
    "ask_analyst": "Analyzing.",
    "ask_strategist": "Thinking it through.",
    "ask_memory_keeper": "Checking what I remember.",
    # self.* — narrate self-modification out loud (Epic 5 x 4.3)
    "self": "Looking at myself.",
    "self.describe": "Looking at my own code.",
    "self.propose_change": "Drafting a change to myself.",
    "self.apply_change": "Applying the change to myself.",
    "self.rollback": "Rolling myself back.",
    "self.request_secret": "I need a key for that.",
}

_FRIENDLY_APPROVAL: dict[str, str] = {
    "gmail.send": "send that email",
    "self.apply_change": "apply that change to my own code",
    "terminal.spawn": "run that on your machine",
}
_FRIENDLY_APPROVAL_NS: dict[str, str] = {
    "gmail": "send that email",
    "self": "change my own code",
    "terminal": "run that command",
}


def narrate_tool(tool: str) -> str | None:
    """Short spoken line for a tool starting, or None if it's not worth voicing.

    Falls back from the full name (`memory.search`) to the connector namespace
    (`memory`), then to a generic line for expert delegations (`ask_*`).
    """
    if not tool:
        return None
    if tool in TOOL_ACK:
        return TOOL_ACK[tool]
    ns = tool.split(".")[0]
    if ns in TOOL_ACK:
        return TOOL_ACK[ns]
    if tool.startswith("ask_"):
        return f"Asking my {tool[4:].replace('_', ' ')}."
    return None


def narrate_approval(name: str) -> str:
    """Spoken prompt when a tool is blocked pending the operator's approval."""
    friendly = _FRIENDLY_APPROVAL.get(name)
    if friendly is None:
        friendly = _FRIENDLY_APPROVAL_NS.get(name.split(".")[0], f"run {name}")
    return f"I need your OK to {friendly}. Say the word."
