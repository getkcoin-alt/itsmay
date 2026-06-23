"""Live self-context. Renders 'who I am right now' for every turn's system prompt."""

from __future__ import annotations

from datetime import date

from core.config import get_settings

VERSION = "0.1.0"


def days_until(d: date) -> int:
    return (d - date.today()).days


def _active_model(s) -> str:  # noqa: ANN001 - Settings, avoids import cycle
    """The model actually serving this turn (provider-aware)."""
    return s.ollama_model if s.llm_provider.lower() == "ollama" else s.llm_model


async def render_self_context(experts: list[str] | None = None) -> str:
    """Compact, model-readable snapshot of the agent's current state.

    `experts` lists the delegation tools currently available (e.g.
    'ask_memory_keeper'), so Scrappy knows which specialists he can hand off to.
    """
    s = get_settings()
    target = s.mission_target_date
    days_left = days_until(target)

    lines = [
        f"Agent: Scrappy Singh  |  Version: {VERSION}  |  Model: {_active_model(s)}",
        "Operator: Karnveer Singh (handle: karnveer). Spell his name exactly: K-A-R-N-V-E-E-R.",
        f"Today: {date.today().isoformat()}",
        f"Mission: {s.mission_statement}",
        f"Target date: {target.isoformat()}  ({days_left} days remaining)",
        "Active capabilities: chat, episodic memory, semantic recall, long-term "
        "memory tools (save/recall), expert delegation, Mac control (voice).",
    ]
    if experts:
        lines.append(
            "Experts you can delegate to via tool call: " + ", ".join(sorted(experts)) + "."
        )
    lines.append("Not yet wired: browser, Google connectors, nightly memory consolidation.")
    return "\n".join(lines)
