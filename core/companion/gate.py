"""The "when to talk vs just observe" policy — Mini AI knowing when to stay quiet.

Pure, deterministic, fully unit-testable. Given a transcribed utterance, the
active profile's nickname, and whether a conversation is currently live, it
decides: **speak** (and hands back the utterance with any leading address
stripped) or **observe** (stay silent — the runtime still saves the memory).

v1 policy (conservative on purpose, so the bot doesn't butt into human
conversations):
    - Addressed by nickname at the start ("Pixel, …" / "hey Pixel …") → speak.
    - Otherwise, if a chat is already live (we spoke seconds ago) → treat as a
      follow-up → speak.
    - Else → observe silently.

Proactively answering ambient questions (not addressed, no live chat) is
deliberately NOT in v1 — that's a later opt-in heuristic.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

# Filler/wake words allowed to precede the nickname in an address.
_WAKE_WORDS = {"hey", "ok", "okay", "yo", "hi", "hello", "um", "uh"}
# How close a heard token must be to the nickname to count (STT mishears names —
# "Pexel" vs "Pixel"). difflib ratio; 1.0 = identical.
_NICK_FUZZ = 0.8


def _is_nickname(token: str, nickname: str) -> bool:
    token, nick = token.lower(), nickname.lower()
    if token == nick:
        return True
    if len(nick) <= 3:
        return False  # too short to fuzzy-match without false positives
    return difflib.SequenceMatcher(None, token, nick).ratio() >= _NICK_FUZZ


@dataclass(slots=True)
class GateDecision:
    speak: bool
    reason: str  # "addressed" | "follow_up" | "observing" | "no_speech"
    cleaned_text: str  # utterance for the LLM, leading address stripped when present


def detect_address(text: str, nickname: str | None) -> tuple[bool, str]:
    """True iff `text` opens by addressing the bot by `nickname` (optionally after
    a wake word), tolerating small STT mishearings of the name. Returns
    (addressed, cleaned_text). A nickname appearing only mid-sentence ("I told
    Pixel earlier") is NOT an address."""
    t = (text or "").strip()
    nick = (nickname or "").strip()
    if not t or not nick:
        return (False, t)
    words = re.findall(r"[a-z0-9']+", t.lower())
    i = 0
    while i < len(words) and words[i] in _WAKE_WORDS:
        i += 1
    if i >= len(words) or not _is_nickname(words[i], nick):
        return (False, t)
    # Strip the leading "(wake )* <name>[ ,:!?]" from the original text. We strip
    # the *heard* tokens (words[:i+1]), so casing/punctuation come out clean.
    prefix = r"\W+".join(re.escape(w) for w in words[: i + 1])
    cleaned = re.sub(rf"^\W*{prefix}\W*", "", t, count=1, flags=re.IGNORECASE).strip()
    return (True, cleaned or t)


def should_respond(
    text: str, *, nickname: str | None, in_active_chat: bool
) -> GateDecision:
    """Decide whether Mini AI should speak to this utterance or just observe it."""
    t = (text or "").strip()
    if not t:
        return GateDecision(False, "no_speech", "")
    addressed, cleaned = detect_address(t, nickname)
    if addressed:
        return GateDecision(True, "addressed", cleaned)
    if in_active_chat:
        return GateDecision(True, "follow_up", t)
    return GateDecision(False, "observing", t)


def is_active_window(
    last_exchange_at: float | None, now: float, window_s: float
) -> bool:
    """A conversation is 'live' if we last exchanged within `window_s` seconds.
    Times are monotonic seconds (e.g. time.monotonic())."""
    return last_exchange_at is not None and (now - last_exchange_at) <= window_s
