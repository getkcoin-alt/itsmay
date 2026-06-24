"""Seed Scrappy's long-term semantic memory from data/knowledge.yaml.

Run:
    python scripts/seed_memory.py           # insert new facts, skip existing
    python scripts/seed_memory.py --wipe    # delete all seed facts, then re-insert

Connects directly to Postgres via core/ — no HTTP server required.
Idempotent: existing entries with identical content are skipped.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root is on the path when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from dotenv import load_dotenv

load_dotenv()

from core.config import get_settings
from core.memory.db import close_pool, get_pool
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.migrate import run_migrations
from core.memory.semantic import SemanticStore

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.yaml"
SEED_SOURCE = "seed:knowledge.yaml"


async def seed(wipe: bool = False) -> None:
    settings = get_settings()

    pool = await get_pool()
    await run_migrations(pool)

    episodic = EpisodicStore()
    semantic = SemanticStore()
    embedder = Embedder()

    user_id = await episodic.get_or_create_user(settings.user_handle)

    if wipe:
        async with pool.acquire() as conn:
            deleted = await conn.fetchval(
                "DELETE FROM memories WHERE user_id = $1 AND source = $2 RETURNING count(*)",
                user_id,
                SEED_SOURCE,
            )
        print(f"Wiped {deleted or 0} existing seed facts.")

    entries = yaml.safe_load(KNOWLEDGE_FILE.read_text())
    inserted = 0
    skipped = 0

    for entry in entries:
        content: str = entry["content"].strip()
        importance: float = float(entry.get("importance", 0.6))
        kind: str = entry.get("kind", "factual")

        # Idempotency: skip if identical content already stored for this user.
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM memories WHERE user_id = $1 AND content = $2 LIMIT 1",
                user_id,
                content,
            )
        if exists:
            skipped += 1
            continue

        embedding = await embedder.embed(content)
        await semantic.write(
            user_id,
            kind,  # type: ignore[arg-type]
            content,
            embedding,
            source=SEED_SOURCE,
            importance=importance,
        )
        inserted += 1
        print(f"  ✓ [{kind:<12}] {importance:.2f}  {content[:70]}...")

    await embedder.aclose()
    await close_pool()
    print(f"\nDone — inserted: {inserted}, skipped (already present): {skipped}")


def main() -> None:
    wipe = "--wipe" in sys.argv
    asyncio.run(seed(wipe=wipe))


if __name__ == "__main__":
    main()
