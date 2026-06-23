"""Assembles the message list sent to the LLM each turn.

Layering (top to bottom of the prompt stack):
    1. System prompt (Scrappy Singh persona, immutable)
    2. Live self-context (mission, capabilities, today's state)
    3. Retrieved semantic memories (relevant to current user turn)
    4. Recent conversation window (episodic, last N turns)
    5. The new user message
"""

from __future__ import annotations

from pathlib import Path

from core.brain.llm import Message

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_system_prompt() -> str:
    return (PROMPTS_DIR / "system_scrappy.md").read_text(encoding="utf-8").strip()


VOICE_MODE_RULES = """## VOICE MODE
The user is *speaking* to you over a microphone. They hear you through a speaker.

Reply rules:
- 1 to 3 short sentences. Never a paragraph. Never bullet lists.
- No markdown, no code blocks, no headings, no emojis.
- Spoken English: contractions, natural pacing, no formal preamble.
- If they ask something technical, give the punchline first and offer to go deeper.
- Skip throat-clearing ("Sure, here's…") — answer directly.
- Treat the conversation like a sharp phone call with a smart operator, not a doc dump."""


def build_messages(
    *,
    self_context: str,
    retrieved_memories: list[str],
    recent_messages: list[Message],
    user_input: str,
    voice_mode: bool = False,
) -> list[Message]:
    """Compose the full prompt for one turn."""
    parts: list[str] = [load_system_prompt(), "", "## CURRENT STATE", self_context.strip()]

    if voice_mode:
        parts += ["", VOICE_MODE_RULES]

    if retrieved_memories:
        parts += ["", "## RELEVANT MEMORIES (retrieved)"]
        parts += [f"- {m}" for m in retrieved_memories]

    system_block = "\n".join(parts)

    msgs: list[Message] = [Message(role="system", content=system_block)]
    msgs.extend(recent_messages)
    msgs.append(Message(role="user", content=user_input))
    return msgs
