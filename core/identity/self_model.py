"""Live self-context. Renders 'who I am right now' for every turn's system prompt."""

from __future__ import annotations

from datetime import date

from core.config import get_settings

VERSION = "0.1.0"


def days_until(d: date) -> int:
    return (d - date.today()).days


async def render_self_context() -> str:
    """Compact, model-readable snapshot of the agent's current state."""
    s = get_settings()
    target = s.mission_target_date
    days_left = days_until(target)

    lines = [
        f"Agent: Scrappy Singh  |  Version: {VERSION}  |  Model: {s.ollama_model}",
        "Operator: Karnveer Singh (handle: karnveer). Spell his name exactly: K-A-R-N-V-E-E-R.",
        f"Today: {date.today().isoformat()}",
        f"Mission: {s.mission_statement}",
        f"Target date: {target.isoformat()}  ({days_left} days remaining)",
        "Active capabilities: chat, episodic memory, semantic recall.",
        "Not yet wired: voice I/O, browser, Mac control, Google connectors, agents, negotiations.",
    ]
    return "\n".join(lines)
