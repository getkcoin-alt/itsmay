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

# Selectable personalities. Each person can pick the vibe of their companion at
# enrollment (stored on the profile); falls back to settings.companion_persona.
# title → shown in `mini profiles`; file → the system prompt.
PERSONAS: dict[str, tuple[str, str]] = {
    "friend": ("Best Friend", "system_mini.md"),  # playful, childish, gentle roasting
    "mentor": ("Mentor", "system_mini_mentor.md"),  # calm, honest, growth-focused
}
DEFAULT_PERSONA = "friend"


def persona_key(name: str | None) -> str:
    """Normalize a persona name to a known key (unknown/empty → default)."""
    key = (name or "").strip().lower()
    return key if key in PERSONAS else DEFAULT_PERSONA


def persona_title(name: str | None) -> str:
    return PERSONAS[persona_key(name)][0]


def load_persona(name: str | None = None) -> str:
    """Load a persona's system prompt by key. Unknown name → the default persona."""
    _, fname = PERSONAS[persona_key(name)]
    return (PROMPTS_DIR / fname).read_text(encoding="utf-8").strip()


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
