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

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from core.companion.gate import GateDecision, is_active_window, should_respond
from core.companion.persona import build_companion_messages, load_persona
from core.companion.profiles import Profile, ProfileStore, ensure_profile_schema
from core.companion.speaker_id import Match, SpeakerIdentifier
from core.logging import get_logger

log = get_logger(__name__)

# Bound the per-profile in-memory maps so a long-running device that meets many
# people (or churns profiles) can't grow them without limit. One device holds a
# handful of people; this is generous. Eviction only drops the cached open-session
# id / last-spoke time — memory itself is durable in SQLite.
_MAX_TRACKED_PROFILES = 256
# Observations shorter than this are too trivial to be worth a semantic row.
_MIN_OBSERVE_CHARS = 12


@dataclass(slots=True)
class Turn:
    spoke: bool
    reason: str  # gate reason: addressed | follow_up | observing | no_speech
    reply: str
    profile_id: str | None


class UnknownVoicePrompter:
    """When `mini run` hears a voice it can't place, we don't cross-contaminate a
    known person's memory — but we shouldn't silently ignore them either. This
    decides *when* to surface an actionable 'run `mini enroll`' hint, throttled so
    a stranger talking a lot doesn't spam the console: the hint shows on the first
    unknown voice, then at most once per `cooldown_s`. Pure + testable (no audio).
    """

    def __init__(self, cooldown_s: float = 30.0) -> None:
        self.cooldown_s = cooldown_s
        self._last_hint_at: float | None = None

    def prompt(self, now: float) -> str | None:
        """Return the hint string to print for this unknown utterance, or None to
        stay quiet (still within the cooldown)."""
        if self._last_hint_at is not None and (now - self._last_hint_at) < self.cooldown_s:
            return None
        self._last_hint_at = now
        return "I don't recognise this voice yet — run `mini enroll` so I can remember you."


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
        self._sessions: OrderedDict[str, UUID] = OrderedDict()  # profile_id -> session_id
        self._last_spoke: OrderedDict[str, float] = OrderedDict()  # profile_id -> monotonic ts
        self._session_locks: dict[str, asyncio.Lock] = {}  # per-profile open-session guard

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
        self, profile: Profile, text: str, *, mode: str = "auto", now: float | None = None
    ) -> Turn:
        """Process one utterance. `mode` overrides the speak/observe decision:
        - "talk"    → always reply (live conversation, no nickname needed)
        - "observe" → never reply, just listen + remember (the press-O mode)
        - "auto"    → the nickname gate (`should_respond`) decides
        """
        text = (text or "").strip()
        if not text:
            return Turn(False, "no_speech", "", profile.id)
        now = now if now is not None else time.monotonic()

        if mode == "observe":
            decision = GateDecision(False, "observe", text)
        elif mode == "talk":
            decision = GateDecision(True, "talk", text)
        else:
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

        # Always remember what they said — observing is still listening. Keep the
        # utterance in the episodic window (embedded, so recall works), but only
        # write a durable semantic row when it's worth recalling later — a guard
        # against a chatty device flooding the store with trivia/duplicates.
        await self.episodic.append_message(session_id, "user", text, embedding=emb)
        if await self._worth_remembering(profile, text):
            await self.semantic.write(
                profile.user_id, "episodic", text, emb, source="observed", importance=0.4
            )
        if decision.speak and reply:
            # No embedding on the assistant turn: nothing vector-searches assistant
            # messages, so embedding the reply is wasted work (a whole extra embed
            # per spoken turn). It still lands in the episodic window verbatim.
            await self.episodic.append_message(session_id, "assistant", reply)
            self._remember_spoke(profile.id, now)

        await self.profiles.touch(profile.id)
        return Turn(decision.speak, decision.reason, reply, profile.id)

    async def _worth_remembering(self, profile: Profile, text: str) -> bool:
        """Cheap guard so observing a chatty room doesn't grow unbounded: skip
        trivially short utterances and exact duplicates we've already stored."""
        if len(text) < _MIN_OBSERVE_CHARS:
            return False
        try:
            if await self.semantic.content_exists(profile.user_id, text):
                return False
        except Exception:  # dedup is best-effort — never block remembering on it
            pass
        return True

    def _remember_spoke(self, profile_id: str, now: float) -> None:
        self._last_spoke[profile_id] = now
        self._last_spoke.move_to_end(profile_id)
        while len(self._last_spoke) > _MAX_TRACKED_PROFILES:
            self._last_spoke.popitem(last=False)

    async def _session_for(self, profile: Profile) -> UUID:
        # Guard the check-then-open with a per-profile lock so two near-simultaneous
        # utterances for the same person can't each open a fresh session.
        sid = self._sessions.get(profile.id)
        if sid is not None:
            self._sessions.move_to_end(profile.id)
            return sid
        lock = self._session_locks.setdefault(profile.id, asyncio.Lock())
        async with lock:
            sid = self._sessions.get(profile.id)  # re-check inside the lock
            if sid is None:
                sid = await self.episodic.open_session(profile.user_id, channel="mini")
                self._sessions[profile.id] = sid
                while len(self._sessions) > _MAX_TRACKED_PROFILES:
                    evicted, _ = self._sessions.popitem(last=False)
                    self._session_locks.pop(evicted, None)
            self._sessions.move_to_end(profile.id)
            return sid

    async def _generate(self, messages) -> str:
        parts: list[str] = []
        async for chunk in self.llm.chat_stream(messages, temperature=0.7):
            parts.append(chunk.delta)
        return "".join(parts).strip() or "…"


def build_engine(*, path: str | None = None) -> CompanionEngine:
    """Wire a companion engine from settings (Ollama or Cloud LLM, local embedder, SQLite
    profiles+memory)."""
    from core.brain.llm import LLMClient
    from core.config import get_settings
    from core.memory.embedder import Embedder
    from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore

    s = get_settings()
    resolved = ensure_profile_schema(path or s.companion_sqlite_path)

    # Determine LLM configuration
    provider = s.companion_provider
    
    if provider == "ollama":
        base_url = s.companion_base_url or s.ollama_host
        api_key = s.companion_api_key or None
        model = s.companion_model
    else:
        # Cloud/OpenAI-compatible (e.g., Groq, OpenRouter)
        base_url = s.companion_base_url or s.llm_base_url
        api_key = s.companion_api_key or s.llm_api_key
        # If using cloud, default companion_model to llama-3.1-8b-instant if not overridden
        model = s.companion_model
        if model == "llama3.2:3b":  # The default local model name
            model = s.llm_agent_model or "llama-3.1-8b-instant"

    return CompanionEngine(
        profiles=ProfileStore(resolved),
        semantic=SqliteSemanticStore(resolved),
        episodic=SqliteEpisodicStore(resolved),
        embedder=Embedder(),
        llm=LLMClient(provider=provider, base_url=base_url, api_key=api_key, model=model),
        identifier=SpeakerIdentifier(),
        active_window_s=s.companion_active_window_s,
    )
