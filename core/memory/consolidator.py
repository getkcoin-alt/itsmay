"""Nightly memory consolidation — critic pass over today's conversation.

`consolidate_today` reads all messages from today's sessions for a user,
sends them to the LLM with a consolidation prompt, and persists the extracted
facts as semantic memories. Safe to call any time; idempotent on same content.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID

from core.brain.llm import LLMClient, Message
from core.logging import get_logger
from core.memory.db import get_pool
from core.memory.embedder import Embedder
from core.memory.semantic import SemanticStore

log = get_logger(__name__)

_CONSOLIDATION_PROMPT = """\
Below is a transcript of today's conversation. Extract 3–8 durable facts worth
remembering long-term as distinct memories for the AI assistant.

Rules:
- Each fact must be a single, self-contained sentence a stranger can understand
  without reading the conversation.
- Only extract facts that are stable (preferences, decisions, identities, goals,
  project details, recurring contacts). Skip ephemeral chat and one-off tasks.
- Assign importance 0.5–0.9: identity/goals = 0.8+, stack/projects = 0.7,
  preferences/trivia = 0.5–0.6.
- Allowed kinds: factual | procedural | semantic | reflection

Respond with ONLY a JSON array (no markdown fences, no extra text):
[{"content": "...", "importance": 0.8, "kind": "factual"}, ...]

TRANSCRIPT:
{transcript}
"""


async def consolidate_today(
    llm: LLMClient,
    semantic: SemanticStore,
    embedder: Embedder,
    user_id: UUID,
) -> dict[str, int]:
    """Extract durable facts from today's messages and save them as memories.

    Returns {"extracted": N, "saved": N, "skipped": N}.
    """
    pool = await get_pool()

    # Fetch today's user+assistant messages across all sessions.
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.role, m.content
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE s.user_id = $1
              AND m.created_at >= $2
              AND m.role IN ('user', 'assistant')
            ORDER BY m.created_at ASC
            """,
            user_id,
            today_start,
        )

    if not rows:
        log.info("consolidate.no_messages_today", user_id=str(user_id))
        return {"extracted": 0, "saved": 0, "skipped": 0}

    # Build transcript string (capped at 8000 chars to stay within context).
    lines: list[str] = []
    for r in rows:
        prefix = "User" if r["role"] == "user" else "Scrappy"
        lines.append(f"{prefix}: {r['content'][:500]}")
    transcript = "\n".join(lines)[:8000]

    # Ask the LLM for facts. .replace (not .format): the prompt's JSON example
    # contains literal braces that str.format would misread as fields.
    prompt = _CONSOLIDATION_PROMPT.replace("{transcript}", transcript)
    messages = [
        Message(role="system", content="You are a memory extraction assistant."),
        Message(role="user", content=prompt),
    ]

    full_text: list[str] = []
    async for chunk in llm.chat_stream(messages, temperature=0.2):
        if chunk.delta:
            full_text.append(chunk.delta)

    raw = "".join(full_text).strip()

    # Parse JSON — tolerate markdown fences if the model adds them.
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        facts: list[dict] = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("consolidate.parse_error", err=str(e), raw=raw[:200])
        return {"extracted": 0, "saved": 0, "skipped": 0}

    extracted = len(facts)
    saved = 0
    skipped = 0

    for fact in facts:
        content = str(fact.get("content", "")).strip()
        importance = float(fact.get("importance", 0.6))
        kind = str(fact.get("kind", "factual"))

        if not content:
            skipped += 1
            continue

        # Idempotency: skip if exact content already exists.
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM memories WHERE user_id = $1 AND content = $2 LIMIT 1",
                user_id,
                content,
            )
        if exists:
            skipped += 1
            continue

        try:
            embedding = await embedder.embed(content)
            await semantic.write(
                user_id,
                kind,  # type: ignore[arg-type]
                content,
                embedding,
                source="consolidator",
                importance=importance,
            )
            saved += 1
        except Exception as e:
            log.warning("consolidate.save_error", err=str(e), content=content[:80])
            skipped += 1

    log.info("consolidate.done", extracted=extracted, saved=saved, skipped=skipped)
    return {"extracted": extracted, "saved": saved, "skipped": skipped}
