"""Per-person profiles — Mini AI's address book.

A *profile* binds a person's **voiceprint** to (a) their own **memory namespace**
(a `users` row, reusing the SQLite memory store) and (b) the **nickname that
person gave the bot**. One device holds many profiles; `core.companion.speaker_id`
maps an incoming voice to the right one. "Start from scratch with every new
individual" = a fresh profile (and fresh memory) per unrecognized voice.

Profiles live in the *same* SQLite file as the memory store (one file per device),
as an additive `voice_profiles` table — so a person's profile and their memories
share a DB and cascade together when the person is forgotten.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import numpy as np

from core.logging import get_logger
from core.memory.episodic import EpisodicStore
from core.memory.sqlite_store import (
    SqliteEpisodicStore,
    _connect,
    _from_blob,
    _now,
    _parse_ts,
    _to_blob,
)
from core.memory.sqlite_store import ensure_schema as ensure_memory_schema

log = get_logger(__name__)

_PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_profiles (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    person_name   TEXT,
    bot_nickname  TEXT,
    persona       TEXT,
    voiceprint    BLOB,
    sample_count  INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_voice_profiles_user ON voice_profiles (user_id);
"""


def ensure_profile_schema(path: str) -> str:
    """Create the memory schema (users/sessions/…) AND the voice_profiles table.
    Idempotent. Returns the resolved absolute path."""
    resolved = ensure_memory_schema(path)  # users table must exist for the FK
    with closing(_connect(resolved)) as conn, conn:
        conn.executescript(_PROFILE_SCHEMA)
        # Additive: bring older DBs (pre-persona) up to date without a migration.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(voice_profiles)")}
        if "persona" not in cols:
            conn.execute("ALTER TABLE voice_profiles ADD COLUMN persona TEXT")
    log.info("companion.profile_schema_ready", path=resolved)
    return resolved


@dataclass(slots=True)
class Profile:
    id: str
    user_id: UUID  # the person's memory namespace (users.id)
    person_name: str | None
    bot_nickname: str | None
    persona: str | None  # personality key (see core.companion.persona.PERSONAS)
    voiceprint: np.ndarray | None
    sample_count: int
    created_at: datetime
    last_seen_at: datetime | None


def _row_to_profile(r) -> Profile:
    keys = r.keys()
    return Profile(
        id=r["id"],
        user_id=UUID(r["user_id"]),
        person_name=r["person_name"],
        bot_nickname=r["bot_nickname"],
        persona=r["persona"] if "persona" in keys else None,
        voiceprint=_from_blob(r["voiceprint"]),
        sample_count=int(r["sample_count"]),
        created_at=_parse_ts(r["created_at"]) or datetime.now().astimezone(),
        last_seen_at=_parse_ts(r["last_seen_at"]),
    )


class ProfileStore:
    """CRUD over `voice_profiles`, plus creating each person's memory namespace.

    Composes `SqliteEpisodicStore` so a new person automatically gets a fresh
    `users` row (their memory namespace) when their profile is created.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._episodic: EpisodicStore = SqliteEpisodicStore(path)

    # ── create / enroll ──────────────────────────────────────────
    async def create_person(
        self,
        *,
        person_name: str | None,
        bot_nickname: str | None,
        voiceprint: np.ndarray | None,
        persona: str | None = None,
    ) -> Profile:
        """Create a brand-new person: a fresh memory namespace + a voice profile."""
        profile_id = uuid4().hex
        handle = f"mini-{profile_id[:12]}"
        user_id = await self._episodic.get_or_create_user(handle)
        return await asyncio.to_thread(
            self._insert, profile_id, user_id, person_name, bot_nickname, persona, voiceprint
        )

    def _insert(
        self,
        profile_id: str,
        user_id: UUID,
        person_name: str | None,
        bot_nickname: str | None,
        persona: str | None,
        voiceprint: np.ndarray | None,
    ) -> Profile:
        now = _now()
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO voice_profiles
                    (id, user_id, person_name, bot_nickname, persona, voiceprint,
                     sample_count, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    profile_id,
                    str(user_id),
                    person_name,
                    bot_nickname,
                    persona,
                    _to_blob(voiceprint),
                    now,
                    now,
                ),
            )
        return Profile(
            id=profile_id,
            user_id=user_id,
            person_name=person_name,
            bot_nickname=bot_nickname,
            persona=persona,
            voiceprint=None if voiceprint is None else np.asarray(voiceprint, dtype=np.float32),
            sample_count=1,
            created_at=_parse_ts(now) or datetime.now().astimezone(),
            last_seen_at=_parse_ts(now),
        )

    # ── read ─────────────────────────────────────────────────────
    async def all(self) -> list[Profile]:
        return await asyncio.to_thread(self._all)

    def _all(self) -> list[Profile]:
        with closing(_connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT * FROM voice_profiles ORDER BY last_seen_at DESC"
            ).fetchall()
            return [_row_to_profile(r) for r in rows]

    async def get(self, profile_id: str) -> Profile | None:
        return await asyncio.to_thread(self._get, profile_id)

    def _get(self, profile_id: str) -> Profile | None:
        with closing(_connect(self.path)) as conn:
            r = conn.execute(
                "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return _row_to_profile(r) if r else None

    # ── update ───────────────────────────────────────────────────
    async def blend_voiceprint(self, profile_id: str, sample: np.ndarray) -> None:
        """Fold a new voice sample into the stored print as a running average, so
        recognition sharpens the more we hear someone."""
        await asyncio.to_thread(self._blend_voiceprint, profile_id, sample)

    def _blend_voiceprint(self, profile_id: str, sample: np.ndarray) -> None:
        sample = np.asarray(sample, dtype=np.float32)
        with closing(_connect(self.path)) as conn, conn:
            r = conn.execute(
                "SELECT voiceprint, sample_count FROM voice_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
            if r is None:
                return
            n = int(r["sample_count"])
            prev = _from_blob(r["voiceprint"])
            blended = sample if prev is None else (prev * n + sample) / (n + 1)
            conn.execute(
                "UPDATE voice_profiles SET voiceprint = ?, sample_count = ?, last_seen_at = ? "
                "WHERE id = ?",
                (_to_blob(blended), n + 1, _now(), profile_id),
            )

    async def set_nickname(self, profile_id: str, nickname: str) -> None:
        await asyncio.to_thread(self._set_field, profile_id, "bot_nickname", nickname)

    async def set_person_name(self, profile_id: str, name: str) -> None:
        await asyncio.to_thread(self._set_field, profile_id, "person_name", name)

    async def set_persona(self, profile_id: str, persona: str) -> None:
        await asyncio.to_thread(self._set_field, profile_id, "persona", persona)

    def _set_field(self, profile_id: str, field: str, value: str) -> None:
        # `field` is an internal literal ("bot_nickname"/"person_name"), never user input.
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                f"UPDATE voice_profiles SET {field} = ? WHERE id = ?", (value, profile_id)
            )

    async def touch(self, profile_id: str) -> None:
        await asyncio.to_thread(self._touch, profile_id)

    def _touch(self, profile_id: str) -> None:
        with closing(_connect(self.path)) as conn, conn:
            conn.execute(
                "UPDATE voice_profiles SET last_seen_at = ? WHERE id = ?",
                (_now(), profile_id),
            )

    # ── delete (forget a person) ─────────────────────────────────
    async def forget(self, profile_id: str) -> bool:
        """Delete the person entirely: removing their `users` row cascades the
        profile, sessions, messages, and memories. Returns True if found."""
        return await asyncio.to_thread(self._forget, profile_id)

    def _forget(self, profile_id: str) -> bool:
        with closing(_connect(self.path)) as conn, conn:
            r = conn.execute(
                "SELECT user_id FROM voice_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if r is None:
                return False
            conn.execute("DELETE FROM users WHERE id = ?", (r["user_id"],))
            return True

    async def wipe_all(self) -> int:
        """Erase every profile + all memories on this device. Returns how many
        profiles were removed."""
        return await asyncio.to_thread(self._wipe_all)

    def _wipe_all(self) -> int:
        with closing(_connect(self.path)) as conn, conn:
            n = conn.execute("SELECT count(*) FROM voice_profiles").fetchone()[0]
            conn.execute("DELETE FROM users")  # cascades sessions/messages/memories/profiles
            return int(n)
