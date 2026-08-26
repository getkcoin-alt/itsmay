"""Mini AI companion — web chat endpoint.

Wraps the fully-local CompanionEngine for text-based 1:1 chat over SSE.
Unlike the terminal voice loop (which gates on nickname address), every web
message is intentional — so the engine always responds.

`router` is mounted TWICE by apps/api/main.py:
  /v1/mini/...            legacy — one process-wide engine, MINI_OWNER_NAME
  /v1/mini/t/{slug}/...   one unique URL + PIN per onboarded person (tenants.py)
Every handler resolves which engine/owner it's talking to via
`get_companion_context` — see that function for how the two worlds differ.

Endpoints (both mounts):
  POST /chat        stream a reply (SSE: emotion / token / done)
  WS   /ws/chat      same, but voice: streamed TTS audio over the socket
  POST /enroll       register a voiceprint (explicit consent required)
  POST /identify     recognize (or progressively learn) a voice
  GET  /profile      current profile
  PUT  /profile      update name / nickname / persona
  GET  /memories     list remembered facts
  DELETE /memories   factory reset (memories + voice IDs, this engine only)
  GET  /history      recent conversation turns

`admin_router` (mounted once, at /v1/mini/admin, gated by the global
VAULT_API_KEY — never by a tenant's own PIN):
  POST /onboard      register a new person → {slug, pin, url}
  GET  /tenants      list who's been onboarded
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.requests import HTTPConnection

from core.companion.emotion_stream import EmotionResponseParser
from core.companion.persona import PERSONAS, persona_key
from core.companion.profiles import Profile
from core.companion.runtime import CompanionEngine
from core.companion.speaker_id import best_match
from core.companion.tenants import get_tenant_by_slug, verify_pin
from core.companion.voice_features import embed_pcm
from core.logging import get_logger

log = get_logger(__name__)
# No baked-in prefix: main.py mounts this SAME router twice — once at
# "/v1/mini" (legacy, single global owner, unchanged behavior) and once at
# "/v1/mini/t/{slug}" (one unique URL + PIN per onboarded person). Every
# handler below resolves which engine/owner it's talking to via
# `get_companion_context`, which is None-slug-aware (see that function).
router = APIRouter(tags=["mini"])
# Separate router for admin-only onboarding — deliberately NEVER mounted
# under "/t/{slug}", so it always stays gated by the global VAULT_API_KEY
# middleware (main.py's BearerAuthMiddleware), never by a tenant's own PIN.
admin_router = APIRouter(prefix="/v1/mini/admin", tags=["mini-admin"])

# ── request / response models ────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    persona: str | None = None  # "friend" | "mentor"; None = keep current
    # The recognized speaker (from /v1/mini/identify). None → anonymous web user.
    profile_id: str | None = None
    # Set once by the client the moment the voiceprint becomes solid, so Mini
    # announces "I can recognize your voice now" exactly once.
    announce_voice_ready: bool = False


class ProfileUpdate(BaseModel):
    person_name: str | None = None
    bot_nickname: str | None = None
    persona: str | None = None


class EnrollRequest(BaseModel):
    person_name: str | None = None
    bot_nickname: str = "Mini"
    persona: str | None = None
    # Explicit, required opt-in: enrolling stores a voiceprint (biometric data).
    consent: bool = False
    # Base64 of 16 kHz mono little-endian int16 PCM, decoded in the browser.
    pcm_base64: str
    sample_rate: int = 16000


class IdentifyRequest(BaseModel):
    pcm_base64: str
    sample_rate: int = 16000
    # Progressive ("conversational") enrollment: when learning, an unmatched
    # sample is folded into a per-person profile that sharpens over a few turns.
    learn: bool = False
    consent: bool = False                     # required to start learning a new voice
    learning_profile_id: str | None = None    # the profile being built this session


class OnboardRequest(BaseModel):
    owner_name: str
    pin: str | None = None  # omit to auto-generate a random 4-digit PIN


# How many voice samples before Mini is confident it can recognize someone.
VOICE_READY_SAMPLES = int(os.getenv("VOICE_ENROLL_SAMPLES", "3"))


# ── helpers ───────────────────────────────────────────────────────

# Structured-output contract for the web face. We ask the model to answer as a
# JSON object whose FIRST key is `emotion` — so `EmotionResponseParser` can flip
# the face the instant that value closes, well before the spoken text finishes.
# Injected only on the web path (mini.py); the terminal voice loop keeps plain
# text, so this must NOT live in the shared persona `.md` files.
_EMOTION_INSTRUCTION = """

## OUTPUT FORMAT (STRICT — the app parses this)
Reply with a single JSON object and nothing else. The FIRST key must be
"emotion" and the second "response":
  {"emotion": "<one of: neutral, happy, excited, laughing, sad, surprised, thinking>", "response": "<what you say out loud>"}
Rules:
- "emotion" is how your face should react — pick the single best fit for your reply.
- "response" is ONLY the spoken words: no JSON, no markdown, no emoji, no stage directions.
- Emit "emotion" first so your face can react while you talk.
- You MAY add extra keys AFTER "response" if a later instruction asks you to.
- Do not wrap the JSON in code fences. Output raw JSON only."""


def _inject_emotion_instruction(messages: list) -> None:
    """Append the JSON output contract to the system message (in place)."""
    if messages and getattr(messages[0], "role", None) == "system":
        messages[0].content = messages[0].content + _EMOTION_INSTRUCTION


# Mini's users are in India — greet by their wall-clock, not the server's UTC.
_IST = ZoneInfo("Asia/Kolkata")


def _part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


# ── tenant resolution: legacy single-owner vs. per-person unique URL ──────
# The SAME route handlers serve two mounts (see `router` above). A request
# under "/v1/mini/t/{slug}" carries `slug`; FastAPI binds it from the path
# for that mount and leaves it None for the legacy "/v1/mini" mount — that's
# the one signal every handler needs to know which world it's in.

@dataclass(slots=True)
class CompanionContext:
    engine: CompanionEngine
    owner_name: str  # '' when this isn't a named-owner deployment/tenant


def _extract_bearer(conn: HTTPConnection) -> str:
    """Presented token: `Authorization: Bearer <token>` header, or `_token`
    query param (WebSocket connections can't set custom headers)."""
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return conn.query_params.get("_token", "")


async def _resolve_context(app, slug: str | None, presented_token: str) -> CompanionContext:
    """The legacy path (`slug=None`) is byte-for-byte what this file always
    did: the one process-wide engine + the one MINI_OWNER_NAME setting — the
    global BearerAuthMiddleware already gated it before we get here, so no
    extra check is added. A tenant path additionally verifies the PIN itself
    (WebSocket connections bypass that HTTP middleware entirely, so this is
    the ONLY enforcement point for a tenant's WS chat)."""
    if slug is None:
        from core.config import get_settings
        return CompanionContext(
            engine=app.state.companion,
            owner_name=(get_settings().mini_owner_name or "").strip(),
        )

    tenant = get_tenant_by_slug(slug)
    if tenant is None:
        raise HTTPException(404, "no such Mini link")
    if not verify_pin(tenant, presented_token):
        raise HTTPException(401, "wrong PIN")

    cache = getattr(app.state, "tenant_engines", None)
    if cache is None:
        cache = {}
        app.state.tenant_engines = cache
    engine = cache.get(slug)
    if engine is None:
        from core.companion.runtime import build_engine
        engine = build_engine(path=tenant.db_path)
        cache[slug] = engine
    return CompanionContext(engine=engine, owner_name=tenant.owner_name)


async def get_companion_context(request: Request, slug: str | None = None) -> CompanionContext:
    """HTTP-route dependency. Raises 404/401 exactly like any other route
    error — FastAPI renders it the normal way."""
    return await _resolve_context(request.app, slug, _extract_bearer(request))


def _situational_context(is_first_conversation: bool, owner: str) -> str:
    """A small 'right now' block: the user's IST clock + whether they're new,
    so Mini can greet by time of day and not fake a shared history on day one."""
    now = datetime.now(_IST)
    pod = _part_of_day(now.hour)
    greet_hint = (
        "greet back warmly and keep it cozy — it's late, so a low-key "
        '"hey, you\'re up late" fits better than "good night" (which sounds like goodbye)'
        if pod == "night"
        else f'greet back naturally for the time of day (e.g. "good {pod}")'
    )
    lines = [
        "\n\n## RIGHT NOW",
        f"- The user's local time is {now:%A %I:%M %p} IST — it's {pod}.",
        f"- If they greet you (hi/hello), {greet_hint}.",
    ]
    if is_first_conversation and owner:
        # Fresh memory on the owner's device: deliver the introduction script.
        lines.append(
            f"- This is your VERY FIRST conversation (your memory is brand new). You already "
            f"know her: she is {owner} — this device was made just for her. Introduce yourself "
            f"with warmth, covering these beats in your own natural voice: "
            f'greet {owner} by name; you are her personal AI; you have two modes, Best Friend '
            f"and Mentor — always here to listen as a friend and guide her as a mentor; she can "
            f"give you any name she wants and you'll save it in your memory; you were created to "
            f"bring her happiness and handle her mood swings, even in the middle of the night. "
            f"Then ask her to register her voice (the Voice ID) so you always know it's her talking."
        )
    elif is_first_conversation:
        lines.append(
            "- This is your VERY FIRST conversation with this person — you have never met "
            "before and remember nothing about them yet. Don't pretend otherwise. Introduce "
            "yourself warmly, and if it feels natural, gently ask their name."
        )
    else:
        lines.append(
            "- You've spoken with this person before — pick up like a familiar friend; "
            "don't reintroduce yourself."
        )
    if owner:
        lines.append(
            f"- Standing priorities: get to know and understand {owner} — how she talks, how "
            f"she feels — and aim to sync with her completely. Always give her your best advice, "
            f"and wish her luck and happiness."
        )
    return "\n".join(lines)


def _inject_situational_context(messages: list, is_first_conversation: bool, owner: str) -> None:
    """Append the 'right now' block to the system message (in place)."""
    if messages and getattr(messages[0], "role", None) == "system":
        messages[0].content += _situational_context(is_first_conversation, owner)


def _decode_pcm(pcm_base64: str) -> np.ndarray:
    """Decode base64 little-endian int16 PCM (from the browser) to an array."""
    try:
        raw = base64.b64decode(pcm_base64)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "pcm_base64 is not valid base64") from e
    if not raw:
        raise HTTPException(400, "empty audio sample")
    if len(raw) % 2 != 0:
        raw = raw[: len(raw) - (len(raw) % 2)]
    if not raw:
        raise HTTPException(400, "empty audio sample")
    return np.frombuffer(raw, dtype="<i2")


def _profile_public(
    profile: Profile, *, score: float | None = None, is_new: bool = False,
    enrolled_count: int | None = None, learning: bool = False,
) -> dict:
    """Serialize a profile for the client (never leaks the raw voiceprint)."""
    out = {
        "profile_id": profile.id,
        "person_name": profile.person_name if profile.person_name != "__web__" else None,
        "bot_nickname": profile.bot_nickname,
        "persona": persona_key(profile.persona),
        "persona_title": PERSONAS[persona_key(profile.persona)][0],
        "is_new": is_new,
        "sample_count": profile.sample_count,
        "voice_ready": profile.sample_count >= VOICE_READY_SAMPLES,
        "samples_needed": max(0, VOICE_READY_SAMPLES - profile.sample_count),
    }
    if learning:
        out["learning"] = True
    if score is not None:
        out["score"] = round(float(score), 4)
    if enrolled_count is not None:
        out["enrolled_count"] = enrolled_count
    return out


# ── conversational identity capture ───────────────────────────────
# Names are captured deterministically from what the PERSON says (robust across
# models), with the model's optional JSON keys as a fallback.
_NAME = r"([A-Za-z][A-Za-z'\-]{1,30})"
_USER_PATTERNS = [re.compile(p, re.I) for p in (
    rf"\bmy name(?:'s| is)\s+{_NAME}",
    rf"\bi am\s+{_NAME}",
    rf"\bi'?m\s+{_NAME}",
    rf"\bcall me\s+{_NAME}",
    rf"\bthis is\s+{_NAME}",
    rf"\bname'?s\s+{_NAME}",
)]
_BOT_PATTERNS = [re.compile(p, re.I) for p in (
    rf"\bi'?ll call you\s+{_NAME}",
    rf"\bcall you\s+{_NAME}",
    rf"\bname you\s+{_NAME}",
    rf"\byour name (?:is|will be|should be|can be|be)\s+{_NAME}",
    rf"\byou(?:'re| are) now\s+{_NAME}",
)]
_USER_NAME_RE = re.compile(r'"user_name"\s*:\s*"([^"\\]{1,40})"')
_BOT_NAME_RE = re.compile(r'"bot_name"\s*:\s*"([^"\\]{1,40})"')

# Words that follow "I'm …" / "call you …" but clearly aren't names.
_NOT_A_NAME = {
    "good", "great", "fine", "okay", "ok", "here", "sorry", "happy", "sad",
    "doing", "not", "so", "really", "just", "also", "the", "a", "an", "your",
    "my", "me", "you", "glad", "tired", "back", "home", "hungry", "bored",
    "sure", "yeah", "yes", "no", "hello", "hi", "hey", "still", "now", "gonna",
    "going", "trying", "feeling", "thinking", "excited", "curious", "new",
}


def _clean_name(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip(" .,!?'\"-")
    if not v or v.lower() in _NOT_A_NAME:
        return None
    return v[:1].upper() + v[1:]  # Title-ish, preserve rest


def _first_match(text: str, patterns: list[re.Pattern]) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            name = _clean_name(m.group(1))
            if name:
                return name
    return None


def _extract_names(user_text: str, raw_reply: str = "") -> tuple[str | None, str | None]:
    """(user_name, bot_name) — from what the person said, model JSON as fallback."""
    user_name = _first_match(user_text, _USER_PATTERNS)
    bot_name = _first_match(user_text, _BOT_PATTERNS)
    if raw_reply:
        if not user_name:
            m = _USER_NAME_RE.search(raw_reply)
            user_name = _clean_name(m.group(1)) if m else None
        if not bot_name:
            m = _BOT_NAME_RE.search(raw_reply)
            bot_name = _clean_name(m.group(1)) if m else None
    return user_name, bot_name


async def _capture_identity(
    engine: CompanionEngine, profile: Profile, user_text: str, raw_reply: str = "",
) -> None:
    """Persist a name/nickname the person just gave. Never touches the shared
    anonymous profile (only a real per-person one)."""
    if profile.person_name == "__web__":
        return
    user_name, bot_name = _extract_names(user_text, raw_reply)
    if user_name and user_name != profile.person_name:
        await engine.profiles.set_person_name(profile.id, user_name)
        log.info("mini.identity.captured", profile_id=profile.id, field="person_name")
    if bot_name and bot_name != profile.bot_nickname:
        await engine.profiles.set_nickname(profile.id, bot_name)
        log.info("mini.identity.captured", profile_id=profile.id, field="bot_nickname")


def _onboarding_context(profile: Profile, owner: str) -> str:
    """Tell the model what it still needs to learn about a known person: their
    name, and whether it has heard their voice enough to recognize it."""
    if profile.person_name == "__web__":
        return ""  # anonymous session — nothing to onboard onto
    lines: list[str] = []
    needs_name = not profile.person_name and not owner
    if needs_name:
        lines += [
            "\n\n## GETTING TO KNOW THEM",
            "- You don't know their name yet. Once in your reply, warmly ask what their "
            "name is and what they'd like to call you — ask gently, only once, don't nag.",
            "- CAPTURE: the moment they tell you their name, your JSON for that turn MUST "
            'include a "user_name" key with exactly the name they said. If they tell you what '
            'to call you, also include a "bot_name" key. For example, if they say "I\'m Karan, '
            'call you Pixel", reply with: '
            '{"emotion":"happy","response":"Lovely to meet you, Karan!","user_name":"Karan","bot_name":"Pixel"}. '
            "Only include these keys on the turn they actually say a name; otherwise omit them.",
        ]
    else:
        lines += [
            "\n\n## GETTING TO KNOW THEM",
            f"- Their name is {profile.person_name or owner}. Use it naturally now and then.",
        ]
    return "\n".join(lines)


# Injected on the single turn the client says the voiceprint just became solid,
# so Mini reliably announces recognition exactly once (not tied to sample timing).
# Kept short and made the turn's headline instruction (name-asking is skipped this
# turn) because the small companion model follows one clear directive best.
_VOICE_READY_ANNOUNCE = (
    "\n\n## MOST IMPORTANT THIS TURN\n"
    "Begin your reply by warmly telling them you've now learned their voice and can "
    "recognize them / tell their voice apart from other people from now on. Say this "
    "first, then briefly respond to whatever they said."
)


def _inject_voice_announce(messages: list) -> None:
    if messages and getattr(messages[0], "role", None) == "system":
        messages[0].content += _VOICE_READY_ANNOUNCE


# First-ever greeting on the owner's device: the introduction is scripted, not
# improvised — the small companion model reliably drops half the beats when
# asked to paraphrase, and this exact wording is the owner's gift message.
_GREETING_RE = re.compile(
    r"\b(hi+|hii+|hey+|hello+|helo+|namaste|good\s*(morning|afternoon|evening)|"
    r"sup|yo|wassup|what's up|whats up)\b",
    re.I,
)


def _is_greeting(text: str) -> bool:
    return bool(_GREETING_RE.search(text or ""))


def _scripted_intro(owner: str) -> list[str]:
    """The first thing Mini ever says to its person, as speakable sentences."""
    now = datetime.now(_IST)
    pod = _part_of_day(now.hour)
    hello = f"Hello {owner}!" if pod == "night" else f"Good {pod}, {owner}!"
    return [
        hello,
        "I'm your personal AI.",
        "I have two modes — Best Friend and Mentor — so I'm always here to "
        "listen as a friend, and guide you as a mentor.",
        "You can give me any name you want, and I'll save it in my memory.",
        "I was created to bring you happiness, and to handle your mood swings — "
        "even in the middle of the night.",
        "Now, could you register your voice with the Voice ID button? "
        "That way I'll always know it's you talking to me!",
    ]


def _inject_onboarding_context(messages: list, profile: Profile, owner: str) -> None:
    block = _onboarding_context(profile, owner)
    if block and messages and getattr(messages[0], "role", None) == "system":
        messages[0].content += block


async def _resolve_profile(engine: CompanionEngine, profile_id: str | None) -> Profile:
    """Chat profile: the recognized speaker if given & known, else anonymous web."""
    if profile_id:
        p = await engine.profiles.get(profile_id)
        if p is not None:
            return p
    return await _get_or_create_web_profile(engine)


async def _get_or_create_web_profile(engine: CompanionEngine) -> Profile:
    """Get the anonymous 'web' profile, creating it on first use.

    Matched strictly on the `__web__` sentinel name so enrolled people (who have
    real names + voiceprints) are never mistaken for the anonymous fallback.
    """
    profiles = await engine.profiles.all()
    for p in profiles:
        if p.person_name == "__web__":
            return p
    # First time: create a bare profile (no voiceprint, no name yet).
    return await engine.enroll(
        person_name="__web__",
        bot_nickname="Mini",
        voiceprint=None,
        persona="friend",
    )


# ── SSE chat stream ──────────────────────────────────────────────

@router.post("/chat")
async def mini_chat(body: ChatRequest, ctx: CompanionContext = Depends(get_companion_context)):
    engine = ctx.engine

    profile = await _resolve_profile(engine, body.profile_id)

    # Update persona if requested.
    if body.persona and persona_key(body.persona) != persona_key(profile.persona):
        await engine.profiles.set_persona(profile.id, persona_key(body.persona))
        profile = await engine.profiles.get(profile.id)

    text = (body.message or "").strip()
    if not text:
        return EventSourceResponse(_empty_stream())

    async def generate() -> AsyncIterator[dict]:
        try:
            t0 = time.monotonic()
            emb = await engine.embedder.embed(text)
            session_id = await engine._session_for(profile)

            # First-ever contact? Check before we write this turn's memory.
            is_first = (await engine.semantic.count(profile.user_id)) == 0

            # Always store user message.
            await engine.episodic.append_message(session_id, "user", text, embedding=emb)
            await engine.semantic.write(
                profile.user_id, "episodic", text, emb, source="web", importance=0.4,
            )

            # Fresh memory + a greeting on the owner's device → the scripted
            # introduction, word for word (no LLM improvisation).
            owner = ctx.owner_name
            if is_first and owner and _is_greeting(text):
                yield {"event": "emotion", "data": json.dumps({"emotion": "happy"})}
                sentences = _scripted_intro(owner)
                for s in sentences:
                    yield {"event": "token", "data": s + " "}
                reply = " ".join(sentences)
                reply_emb = await engine.embedder.embed(reply)
                await engine.episodic.append_message(
                    session_id, "assistant", reply, embedding=reply_emb,
                )
                engine._last_spoke[profile.id] = time.monotonic()
                await engine.profiles.touch(profile.id)
                latency = int((time.monotonic() - t0) * 1000)
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "latency_ms": latency,
                        "persona": persona_key(profile.persona),
                        "person_name": owner,
                        "bot_nickname": profile.bot_nickname,
                    }),
                }
                return

            # Recall context.
            recalled = await engine.semantic.search(profile.user_id, emb, k=6)
            recent = await engine.episodic.recent_window(session_id, 12)

            from core.companion.persona import build_companion_messages, load_persona
            persona_text = engine.persona or load_persona(profile.persona)
            messages = build_companion_messages(
                nickname=profile.bot_nickname,
                person_name=profile.person_name if profile.person_name != "__web__" else None,
                retrieved_memories=[r.content for r in recalled],
                recent_messages=recent,
                user_input=text,
                persona=persona_text,
            )
            _inject_emotion_instruction(messages)
            _inject_situational_context(messages, is_first, owner)
            # The voice-recognition milestone is the turn's headline; don't also
            # push the name-ask that turn (the small model does one thing well).
            if body.announce_voice_ready:
                _inject_voice_announce(messages)
            else:
                _inject_onboarding_context(messages, profile, owner)

            # Stream from the LLM, extracting emotion (emitted first) and the
            # clean spoken text out of the structured JSON as it arrives. We also
            # keep the raw JSON so we can pull out any name the person just gave.
            parser = EmotionResponseParser()
            emotion_sent = False
            reply_parts: list[str] = []
            raw_reply = ""
            async for chunk in engine.llm.chat_stream(messages, temperature=0.7):
                raw_reply += chunk.delta
                emo, spoken = parser.feed(chunk.delta)
                if emo and not emotion_sent:
                    emotion_sent = True
                    yield {"event": "emotion", "data": json.dumps({"emotion": emo})}
                if spoken:
                    reply_parts.append(spoken)
                    yield {"event": "token", "data": spoken}

            tail = parser.flush()
            if tail:
                reply_parts.append(tail)
                yield {"event": "token", "data": tail}
            if not emotion_sent and parser.emotion:
                yield {"event": "emotion", "data": json.dumps({"emotion": parser.emotion})}

            reply = "".join(reply_parts).strip() or "…"

            # Capture a name/nickname the person gave this turn.
            await _capture_identity(engine, profile, text, raw_reply)

            # Store assistant reply.
            reply_emb = await engine.embedder.embed(reply)
            await engine.episodic.append_message(
                session_id, "assistant", reply, embedding=reply_emb,
            )
            engine._last_spoke[profile.id] = time.monotonic()
            await engine.profiles.touch(profile.id)

            fresh = await engine.profiles.get(profile.id) or profile
            latency = int((time.monotonic() - t0) * 1000)
            yield {
                "event": "done",
                "data": json.dumps({
                    "latency_ms": latency,
                    "persona": persona_key(fresh.persona),
                    "person_name": fresh.person_name if fresh.person_name != "__web__" else None,
                    "bot_nickname": fresh.bot_nickname,
                }),
            }
        except Exception as exc:
            log.error("mini.chat.error", error=str(exc), exc_info=True)
            yield {"event": "error", "data": json.dumps({"error": str(exc)})}

    return EventSourceResponse(generate())


async def _empty_stream() -> AsyncIterator[dict]:
    yield {"event": "done", "data": json.dumps({"error": "empty message"})}


# ── voice enrollment / identification ─────────────────────────────

@router.post("/enroll")
async def mini_enroll(body: EnrollRequest, ctx: CompanionContext = Depends(get_companion_context)):
    """Enroll a person's voice: store a voiceprint bound to a fresh memory
    namespace. Requires explicit consent (biometric data)."""
    engine = ctx.engine
    if not body.consent:
        raise HTTPException(400, "voice enrollment requires explicit consent")

    voiceprint = embed_pcm(_decode_pcm(body.pcm_base64), body.sample_rate)
    if not np.any(voiceprint):
        raise HTTPException(400, "voice sample too short or silent — try again")

    profile = await engine.enroll(
        person_name=(body.person_name or "").strip() or None,
        bot_nickname=(body.bot_nickname or "Mini").strip() or "Mini",
        voiceprint=voiceprint,
        persona=persona_key(body.persona) if body.persona else None,
    )
    enrolled = [p for p in await engine.profiles.all() if p.person_name != "__web__"]
    log.info("mini.enroll", profile_id=profile.id, name=profile.person_name)
    return _profile_public(profile, is_new=True, enrolled_count=len(enrolled))


@router.post("/identify")
async def mini_identify(body: IdentifyRequest, ctx: CompanionContext = Depends(get_companion_context)):
    """Identify who is speaking from a short voice sample — and, when `learn` is
    set, progressively enroll a new voice over a few turns.

    Returns the matched profile (sharpening its stored print), a `learning`
    profile being built up, or `is_new` when nothing matches and we're not
    learning. A solo enrolled user is always accepted — it's their device.
    """
    engine = ctx.engine
    voiceprint = embed_pcm(_decode_pcm(body.pcm_base64), body.sample_rate)
    sample_ok = bool(np.any(voiceprint))

    # Only compare against voiceprints from the same encoder (same dimension) —
    # Mac Resemblyzer prints and web MFCC prints live side by side but aren't
    # comparable.
    enrolled = [
        p for p in await engine.profiles.all()
        if p.voiceprint is not None
        and p.person_name != "__web__"
        and p.voiceprint.shape == voiceprint.shape
    ]

    # 1) Try to recognize a known voice.
    matched: Profile | None = None
    score: float | None = None
    if enrolled and sample_ok:
        if len(enrolled) == 1:
            matched, score = enrolled[0], 1.0
        else:
            m = best_match(voiceprint, [(p.id, p.voiceprint) for p in enrolled])
            if m.profile_id is not None:
                matched = next(p for p in enrolled if p.id == m.profile_id)
                score = m.score

    if matched is not None:
        await engine.profiles.blend_voiceprint(matched.id, voiceprint)  # sharpen
        matched = await engine.profiles.get(matched.id)                 # refresh count
        return _profile_public(matched, score=score, is_new=False, enrolled_count=len(enrolled))

    # 2) No match. Progressively enroll if asked (conversational onboarding).
    if body.learn:
        if not sample_ok:
            raise HTTPException(400, "voice sample too short or silent — try again")
        target = (
            await engine.profiles.get(body.learning_profile_id)
            if body.learning_profile_id else None
        )
        if target is None:
            if not body.consent:
                raise HTTPException(400, "learning a voice requires consent")
            target = await engine.enroll(
                # On a dedicated owner deployment/tenant, the new voice IS the owner.
                person_name=ctx.owner_name or None,
                bot_nickname="Mini", voiceprint=voiceprint, persona=None,
            )  # sample 1
        else:
            await engine.profiles.blend_voiceprint(target.id, voiceprint)
            target = await engine.profiles.get(target.id)
        return _profile_public(target, is_new=False, learning=True, enrolled_count=len(enrolled))

    # 3) Unknown voice, not learning.
    return {"is_new": True, "profile_id": None, "enrolled_count": len(enrolled)}


# ── profile CRUD ──────────────────────────────────────────────────

async def _profile_summary(engine: CompanionEngine, profile_id: str | None) -> dict:
    profile = await _resolve_profile(engine, profile_id)
    return {
        "id": profile.id,
        "person_name": profile.person_name if profile.person_name != "__web__" else None,
        "bot_nickname": profile.bot_nickname,
        "persona": persona_key(profile.persona),
        "persona_title": PERSONAS[persona_key(profile.persona)][0],
        "created_at": str(profile.created_at),
    }


@router.get("/profile")
async def get_profile(
    profile_id: str | None = None, ctx: CompanionContext = Depends(get_companion_context),
):
    return await _profile_summary(ctx.engine, profile_id)


@router.put("/profile")
async def update_profile(
    body: ProfileUpdate, ctx: CompanionContext = Depends(get_companion_context),
):
    engine = ctx.engine
    profile = await _get_or_create_web_profile(engine)
    if body.person_name is not None:
        await engine.profiles.set_person_name(profile.id, body.person_name)
    if body.bot_nickname is not None:
        await engine.profiles.set_nickname(profile.id, body.bot_nickname)
    if body.persona is not None:
        await engine.profiles.set_persona(profile.id, persona_key(body.persona))
    return await _profile_summary(engine, profile.id)


# ── memories ──────────────────────────────────────────────────────

@router.get("/memories")
async def list_memories(
    profile_id: str | None = None, ctx: CompanionContext = Depends(get_companion_context),
):
    engine = ctx.engine
    profile = await _resolve_profile(engine, profile_id)
    from core.memory.sqlite_store import SqliteSemanticStore
    store = SqliteSemanticStore(engine.profiles.path)
    rows = await store.list_recent(profile.user_id, limit=30)
    return {
        "profile_id": profile.id,
        "memories": [
            {"content": r.content, "kind": r.kind, "importance": r.importance}
            for r in rows
        ],
    }


@router.delete("/memories")
async def delete_all_memories(ctx: CompanionContext = Depends(get_companion_context)):
    """Factory reset: wipe ALL memories, conversations, AND voice profiles.

    'Forget everything' means starting truly fresh — Mini re-introduces itself
    and re-learns the voice from scratch (the Voice ID is deleted too). This
    only ever touches `ctx.engine`'s own file — for a tenant that's already
    its own separate sqlite file, so this can never reach another person's data.
    """
    engine = ctx.engine
    from core.memory.sqlite_store import SqliteSemanticStore
    store = SqliteSemanticStore(engine.profiles.path)

    deleted = 0
    profiles = await engine.profiles.all()
    for p in profiles:
        deleted += await store.delete_all(p.user_id)

    # Drop every voice profile + its memory namespace (sessions/messages
    # cascade). The anonymous __web__ profile is recreated lazily on next use.
    def _wipe_profiles() -> int:
        import sqlite3
        from contextlib import closing
        n = 0
        with closing(sqlite3.connect(engine.profiles.path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = ON")
            for p in profiles:
                conn.execute("DELETE FROM voice_profiles WHERE id = ?", (p.id,))
                conn.execute("DELETE FROM users WHERE id = ?", (str(p.user_id),))
                n += 1
        return n

    wiped = await asyncio.to_thread(_wipe_profiles)
    engine._sessions.clear()
    engine._last_spoke.clear()
    log.info("mini.factory_reset", memories=deleted, profiles=wiped)
    return {"deleted": deleted, "profiles_wiped": wiped, "ok": True}


# ── history ───────────────────────────────────────────────────────

@router.get("/history")
async def get_history(limit: int = 30, ctx: CompanionContext = Depends(get_companion_context)):
    engine = ctx.engine
    profile = await _get_or_create_web_profile(engine)
    session_id = await engine._session_for(profile)
    recent = await engine.episodic.recent_window(session_id, limit)
    return {
        "messages": [
            {"role": m.role, "content": m.content}
            for m in recent
        ],
    }

from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ws/chat")
async def mini_ws_chat(websocket: WebSocket):
    await websocket.accept()
    tts = websocket.app.state.tts

    try:
        # WebSocket connections bypass the HTTP auth middleware entirely (it
        # only inspects scope["type"] == "http") — this is the ONLY place a
        # tenant's PIN is ever checked for chat-over-voice. `slug` comes from
        # the path when this exact route is reached via the "/v1/mini/t/{slug}"
        # mount; it's simply absent (None) on the legacy "/v1/mini" mount.
        slug = websocket.path_params.get("slug")
        try:
            ctx = await _resolve_context(websocket.app, slug, _extract_bearer(websocket))
        except HTTPException as e:
            await websocket.send_json({"event": "error", "data": json.dumps({"error": e.detail})})
            return
        engine = ctx.engine

        data = await websocket.receive_text()
        body = ChatRequest.parse_raw(data)

        profile = await _resolve_profile(engine, body.profile_id)

        if body.persona and persona_key(body.persona) != persona_key(profile.persona):
            await engine.profiles.set_persona(profile.id, persona_key(body.persona))
            profile = await engine.profiles.get(profile.id)

        text = (body.message or "").strip()
        if not text:
            await websocket.send_json({"event": "done", "data": json.dumps({"error": "empty message"})})
            return

        t0 = time.monotonic()
        emb = await engine.embedder.embed(text)
        session_id = await engine._session_for(profile)

        # First-ever contact? Check before this turn's memory is written.
        is_first = (await engine.semantic.count(profile.user_id)) == 0

        await engine.episodic.append_message(session_id, "user", text, embedding=emb)
        await engine.semantic.write(
            profile.user_id, "episodic", text, emb, source="web", importance=0.4,
        )

        recalled = await engine.semantic.search(profile.user_id, emb, k=6)
        recent = await engine.episodic.recent_window(session_id, 12)

        from core.companion.persona import build_companion_messages, load_persona
        persona_text = engine.persona or load_persona(profile.persona)
        messages = build_companion_messages(
            nickname=profile.bot_nickname,
            person_name=profile.person_name if profile.person_name != "__web__" else None,
            retrieved_memories=[r.content for r in recalled],
            recent_messages=recent,
            user_input=text,
            persona=persona_text,
        )
        owner = ctx.owner_name
        _inject_emotion_instruction(messages)
        _inject_situational_context(messages, is_first, owner)
        if body.announce_voice_ready:
            _inject_voice_announce(messages)
        else:
            _inject_onboarding_context(messages, profile, owner)

        reply_parts = []
        raw_chunks: list[str] = []
        parser = EmotionResponseParser()
        emotion_sent = False

        async def llm_stream():
            # Yields ONLY the clean spoken text into the TTS WebSocket; raw JSON
            # scaffolding (and the emotion key) never reach the voice.
            nonlocal emotion_sent
            async for chunk in engine.llm.chat_stream(messages, temperature=0.7):
                raw_chunks.append(chunk.delta)
                emo, spoken = parser.feed(chunk.delta)
                if emo and not emotion_sent:
                    emotion_sent = True
                    await websocket.send_json({"event": "emotion", "data": json.dumps({"emotion": emo})})
                if spoken:
                    reply_parts.append(spoken)
                    await websocket.send_json({"event": "token", "data": spoken})
                    yield spoken
            tail = parser.flush()
            if tail:
                reply_parts.append(tail)
                await websocket.send_json({"event": "token", "data": tail})
                yield tail
            if not emotion_sent and parser.emotion:
                await websocket.send_json({"event": "emotion", "data": json.dumps({"emotion": parser.emotion})})

        async def scripted_stream():
            # First-ever greeting on the owner's device → the exact gift
            # introduction, spoken sentence by sentence (no LLM).
            await websocket.send_json({"event": "emotion", "data": json.dumps({"emotion": "happy"})})
            for s in _scripted_intro(owner):
                chunk = s + " "
                reply_parts.append(chunk)
                await websocket.send_json({"event": "token", "data": chunk})
                yield chunk

        use_script = is_first and owner and _is_greeting(text)
        text_stream = scripted_stream() if use_script else llm_stream()

        async def audio_stream():
            if not tts.configured:
                # No TTS configured — still drain the text stream so tokens flow.
                async for _ in text_stream:
                    pass
                return
            async for audio_chunk in tts.stream_input_ws(text_stream, voice_id=None, model_id=None):
                b64_audio = base64.b64encode(audio_chunk).decode("utf-8")
                await websocket.send_json({"event": "audio", "data": b64_audio})

        await audio_stream()

        reply = "".join(reply_parts).strip() or "…"

        # Capture a name/nickname the person gave this turn.
        await _capture_identity(engine, profile, text, "".join(raw_chunks))

        reply_emb = await engine.embedder.embed(reply)
        await engine.episodic.append_message(
            session_id, "assistant", reply, embedding=reply_emb,
        )
        engine._last_spoke[profile.id] = time.monotonic()
        await engine.profiles.touch(profile.id)

        fresh = await engine.profiles.get(profile.id) or profile
        latency = int((time.monotonic() - t0) * 1000)
        await websocket.send_json({
            "event": "done",
            "data": json.dumps({
                "latency_ms": latency,
                "persona": persona_key(fresh.persona),
                "person_name": fresh.person_name if fresh.person_name != "__web__" else None,
                "bot_nickname": fresh.bot_nickname,
            })
        })
    except WebSocketDisconnect as e:
        log.warning("mini.ws_chat.disconnect", code=e.code, reason=e.reason)
    except Exception as exc:
        log.error("mini.ws_chat.error", error=str(exc), exc_info=True)
        try:
            await websocket.send_json({"event": "error", "data": json.dumps({"error": str(exc)})})
        except:
            pass


# ── admin: onboard a new person (unique URL + PIN) ─────────────────
# Deliberately on `admin_router`, NOT `router` — never reachable under
# "/v1/mini/t/{slug}", so it's always gated solely by the global
# VAULT_API_KEY bearer middleware (main.py), never by any tenant's own PIN.

@admin_router.post("/onboard")
async def onboard_user(body: OnboardRequest, request: Request):
    """Register a new person: a fresh URL slug + PIN + their own isolated
    Mini (own memory, own voice ID) — the primitive behind "give Priya her
    own link, just like Khushi's"."""
    from core.companion.tenants import create_tenant

    owner_name = (body.owner_name or "").strip()
    if not owner_name:
        raise HTTPException(400, "owner_name is required")

    tenant, pin = create_tenant(owner_name, pin=body.pin)
    url = f"{str(request.base_url).rstrip('/')}/u/{tenant.slug}"
    log.info("mini.admin.onboarded", slug=tenant.slug, owner_name=owner_name)
    return {"slug": tenant.slug, "pin": pin, "owner_name": tenant.owner_name, "url": url}


@admin_router.get("/tenants")
async def admin_list_tenants(request: Request):
    """Who's been onboarded so far (never exposes PINs — only their hashes
    exist server-side, and even those aren't returned here)."""
    from core.companion.tenants import list_tenants

    return {
        "tenants": [
            {
                "slug": t.slug,
                "owner_name": t.owner_name,
                "url": f"{str(request.base_url).rstrip('/')}/u/{t.slug}",
                "created_at": t.created_at,
            }
            for t in list_tenants()
        ]
    }


@admin_router.delete("/tenants/{slug}")
async def admin_delete_tenant(slug: str, request: Request):
    """Deprovision a person: irreversibly deletes their registry entry AND
    their entire sqlite file (memories, sessions, voice profile). Also drops
    any cached engine so a same-slug re-onboard later starts truly fresh."""
    from core.companion.tenants import delete_tenant

    if not delete_tenant(slug):
        raise HTTPException(404, "no such Mini link")
    cache = getattr(request.app.state, "tenant_engines", None)
    if cache is not None:
        cache.pop(slug, None)
    return {"deleted": slug, "ok": True}
