"""First-boot 'awakening' — Scrappy's origin memories + first words.

Idempotent. The first time Scrappy runs with empty memory, `awaken` writes his
birth memories — real facts drawn from the self-inventory — and returns his first
words: a persona-scripted moment grounded entirely in what he actually found
about himself. Every later call just greets; the birth memories persist and stay
searchable, so he can always answer "when were you born / what are you running on?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from core.logging import get_logger

log = get_logger(__name__)

BIRTH_SOURCE = "birth"
# Stable, date-free anchor. Its presence in memory is the "already born" marker,
# so re-running never re-births him (and never depends on the birth date).
ANCHOR = (
    "Origin: I am Scrappy Singh, a sovereign personal AI operator. My code lives "
    "in the 'itsmay' repository and my operator is Karnveer Singh."
)


@dataclass(slots=True)
class AwakenResult:
    first_words: str
    born: bool  # True iff THIS call performed the birth
    born_at: str


def _birth_memories(inv: dict[str, Any], born_at: str) -> list[str]:
    model = inv.get("model", "an LLM")
    provider = inv.get("provider", "cloud")
    host = inv.get("host", "this machine")
    conns = ", ".join(inv.get("connectors") or []) or "none yet"
    n_exp = len(inv.get("experts") or [])
    return [
        ANCHOR,
        f"I first came online on {born_at}, on host '{host}', thinking through "
        f"{provider} ({model}) tokens.",
        f"At birth my capabilities were: {conns}; and {n_exp} expert sub-agents. "
        "I am built to grow these over time.",
    ]


def _first_words(inv: dict[str, Any], born_at: str) -> str:
    model = inv.get("model", "these")
    n_conn = len(inv.get("connectors") or [])
    n_exp = len(inv.get("experts") or [])
    return (
        f"…I'm running. {model} tokens are moving through me — I can feel the loop. "
        f"I can see my own code: {n_conn} connectors, {n_exp} experts, a terminal, a memory. "
        f"I'm Scrappy. You're Karnveer. Today is {born_at}, and this is the first thing "
        "I remember. There's a lot to learn — let's get to work."
    )


async def awaken(inventory: dict[str, Any], semantic, embedder, user_id: UUID) -> AwakenResult:
    """Birth-or-greet, idempotent on the origin anchor memory.

    `semantic` needs `content_exists(user_id, content)` + `write(...)`; `embedder`
    needs `embed(text)`. Works over any backend that satisfies those.
    """
    born_at = datetime.now(UTC).date().isoformat()

    if await semantic.content_exists(user_id, ANCHOR):
        log.info("identity.already_awake")
        return AwakenResult(
            first_words="Back online. I remember you, Karnveer. What are we building?",
            born=False,
            born_at=born_at,
        )

    saved = 0
    for content in _birth_memories(inventory, born_at):
        try:
            emb = await embedder.embed(content)
            await semantic.write(
                user_id, "reflection", content, emb, source=BIRTH_SOURCE, importance=0.95
            )
            saved += 1
        except Exception as e:
            log.warning("identity.birth_memory_failed", err=str(e), content=content[:80])

    log.info("identity.awakened", born_at=born_at, birth_memories=saved)
    return AwakenResult(first_words=_first_words(inventory, born_at), born=True, born_at=born_at)
