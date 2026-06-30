"""CompanionEngine — Mini AI's conversation brain, audio-free and testable.

One call, `handle_text(profile, text)`, runs the whole policy for an utterance:
recall this person's memories, decide (via the gate) whether to **speak** or just
**observe**, generate a reply on the local LLM when speaking, and **always**
remember what was said. Speaker identification and enrollment are separate calls.

Everything heavy is injected (stores, embedder, LLM, voice identifier), so tests
drive the engine with fakes — no Ollama, no torch, no audio. The audio capture /
playback loop lives in `apps/mini` (device-only) and calls this engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from core.companion.gate import is_active_window, should_respond
from core.companion.persona import build_companion_messages, load_persona
from core.companion.profiles import Profile, ProfileStore, ensure_profile_schema
from core.companion.speaker_id import Match, SpeakerIdentifier
from core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Turn:
    spoke: bool
    reason: str  # gate reason: addressed | follow_up | observing | no_speech
    reply: str
    profile_id: str | None


class CompanionEngine:
    def __init__(
        self,
        *,
        profiles: ProfileStore,
        semantic,
        episodic,
        embedder,
        llm,
        identifier: SpeakerIdentifier,
        persona: str | None = None,
        active_window_s: float = 30.0,
    ) -> None:
        self.profiles = profiles
        self.semantic = semantic
        self.episodic = episodic
        self.embedder = embedder
        self.llm = llm
        self.identifier = identifier
        self.persona = persona
        self.active_window_s = active_window_s
        self._sessions: dict[str, UUID] = {}  # profile_id -> session_id
        self._last_spoke: dict[str, float] = {}  # profile_id -> monotonic ts

    # ── speaker identification / enrollment ──────────────────────
    async def identify_speaker(
        self, voiceprint: np.ndarray | None
    ) -> tuple[Match, Profile | None]:
        profs = await self.profiles.all()
        if not profs:
            return Match(None, 0.0), None
        if len(profs) == 1:
            # Single-person device: route everything to them. Voiceprint gating
            # only earns its keep once two+ people share a device, and short
            # clips make it unreliable — so don't let it block a solo user.
            only = profs[0]
            return Match(only.id, 1.0), only
        if voiceprint is None:
            return Match(None, 0.0), None
        match = self.identifier.identify(
            voiceprint, [(p.id, p.voiceprint) for p in profs]
        )
        profile = next((p for p in profs if p.id == match.profile_id), None)
        return match, profile

    async def enroll(
        self,
        *,
        person_name: str | None,
        bot_nickname: str | None,
        voiceprint: np.ndarray | None,
        persona: str | None = None,
    ) -> Profile:
        return await self.profiles.create_person(
            person_name=person_name,
            bot_nickname=bot_nickname,
            voiceprint=voiceprint,
            persona=persona,
        )

    # ── one utterance ────────────────────────────────────────────
    async def handle_text(
        self, profile: Profile, text: str, *, now: float | None = None
    ) -> Turn:
        text = (text or "").strip()
        if not text:
            return Turn(False, "no_speech", "", profile.id)
        now = now if now is not None else time.monotonic()

        in_active = is_active_window(
            self._last_spoke.get(profile.id), now, self.active_window_s
        )
        decision = should_respond(
            text, nickname=profile.bot_nickname, in_active_chat=in_active
        )

        session_id = await self._session_for(profile)
        emb = await self.embedder.embed(text)
        # History BEFORE this turn (so the new message isn't duplicated in-context).
        recent = await self.episodic.recent_window(session_id, 12)

        reply = ""
        if decision.speak:
            recalled = await self.semantic.search(profile.user_id, emb, k=6)
            # Each person's chosen personality (engine-level persona overrides, if set).
            persona_text = self.persona or load_persona(profile.persona)
            messages = build_companion_messages(
                nickname=profile.bot_nickname,
                person_name=profile.person_name,
                retrieved_memories=[r.content for r in recalled],
                recent_messages=recent,
                user_input=decision.cleaned_text,
                persona=persona_text,
            )
            reply = await self._generate(messages)

        # Always remember what they said — observing is still listening.
        await self.episodic.append_message(session_id, "user", text, embedding=emb)
        await self.semantic.write(
            profile.user_id, "episodic", text, emb, source="observed", importance=0.4
        )
        if decision.speak and reply:
            reply_emb = await self.embedder.embed(reply)
            await self.episodic.append_message(
                session_id, "assistant", reply, embedding=reply_emb
            )
            self._last_spoke[profile.id] = now

        await self.profiles.touch(profile.id)
        return Turn(decision.speak, decision.reason, reply, profile.id)

    async def _session_for(self, profile: Profile) -> UUID:
        sid = self._sessions.get(profile.id)
        if sid is None:
            sid = await self.episodic.open_session(profile.user_id, channel="mini")
            self._sessions[profile.id] = sid
        return sid

    async def _generate(self, messages) -> str:
        parts: list[str] = []
        async for chunk in self.llm.chat_stream(messages, temperature=0.7):
            parts.append(chunk.delta)
        return "".join(parts).strip() or "…"


def build_engine(*, path: str | None = None) -> CompanionEngine:
    """Wire a fully-local engine from settings (Ollama LLM, local embedder, SQLite
    profiles+memory). Device-only — tests construct CompanionEngine with fakes."""
    from core.brain.llm import LLMClient
    from core.config import get_settings
    from core.memory.embedder import Embedder
    from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore

    s = get_settings()
    resolved = ensure_profile_schema(path or s.companion_sqlite_path)
    return CompanionEngine(
        profiles=ProfileStore(resolved),
        semantic=SqliteSemanticStore(resolved),
        episodic=SqliteEpisodicStore(resolved),
        embedder=Embedder(),
        llm=LLMClient(provider="ollama", base_url=s.ollama_host, model=s.companion_model),
        identifier=SpeakerIdentifier(),
        active_window_s=s.companion_active_window_s,
    )
