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

import re
from dataclasses import dataclass

# Filler/wake words allowed to precede the nickname in an address.
_WAKE = r"(?:hey|ok|okay|yo|hi|hello|um|uh)"


@dataclass(slots=True)
class GateDecision:
    speak: bool
    reason: str  # "addressed" | "follow_up" | "observing" | "no_speech"
    cleaned_text: str  # utterance for the LLM, leading address stripped when present


def detect_address(text: str, nickname: str | None) -> tuple[bool, str]:
    """True iff `text` opens by addressing the bot by `nickname` (optionally after
    a wake word). Returns (addressed, cleaned_text). A nickname appearing only
    mid-sentence ("I told Pixel earlier") is NOT an address."""
    t = (text or "").strip()
    nick = (nickname or "").strip()
    if not t or not nick:
        return (False, t)
    pattern = re.compile(
        rf"^\s*(?:{_WAKE}\s+)*{re.escape(nick)}\b[\s,:;!.?-]*", re.IGNORECASE
    )
    m = pattern.match(t)
    if not m:
        return (False, t)
    remainder = t[m.end():].strip()
    return (True, remainder or t)


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
