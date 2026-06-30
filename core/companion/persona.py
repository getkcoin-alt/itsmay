"""Mini AI's persona + per-turn context assembly.

Loads the best-friend system prompt (`system_mini.md`) and layers in *this
person's* identity — the nickname they gave the bot, their name if known, and the
memories recalled for the current moment — then the recent conversation and the
new utterance. Pure string/Message assembly (no models), so it's unit-testable.
"""

from __future__ import annotations

from pathlib import Path

from core.brain.llm import Message

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "brain" / "prompts"


def load_persona() -> str:
    return (PROMPTS_DIR / "system_mini.md").read_text(encoding="utf-8").strip()


def build_companion_messages(
    *,
    nickname: str | None,
    person_name: str | None,
    retrieved_memories: list[str],
    recent_messages: list[Message],
    user_input: str,
    persona: str | None = None,
) -> list[Message]:
    """Compose the full prompt for one companion turn."""
    parts: list[str] = [persona or load_persona(), "", "## WHO YOU'RE WITH"]

    ident: list[str] = []
    if nickname:
        ident.append(f'They call you "{nickname}" — that\'s your name.')
    if person_name:
        ident.append(f"Their name is {person_name}.")
    else:
        ident.append("You don't know their name yet — ask sometime if it feels natural.")
    parts.append(" ".join(ident))

    if retrieved_memories:
        parts += ["", "## WHAT YOU REMEMBER ABOUT THEM"]
        parts += [f"- {m}" for m in retrieved_memories]

    system_block = "\n".join(parts)
    msgs: list[Message] = [Message(role="system", content=system_block)]
    msgs.extend(recent_messages)
    msgs.append(Message(role="user", content=user_input))
    return msgs
