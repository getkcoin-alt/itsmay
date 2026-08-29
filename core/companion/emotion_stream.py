"""Incremental parser for Mini's structured emotion/response stream.

The web companion asks the model for JSON shaped like::

    {"emotion":"happy","response":"hello there"}

Chunks may split anywhere, including inside escapes.  This parser emits the
emotion once it is a complete JSON string and streams only decoded characters
inside the ``response`` string. JSON keys, punctuation and later metadata are
never returned as spoken text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_ALLOWED_EMOTIONS = {
    "neutral",
    "happy",
    "excited",
    "laughing",
    "sad",
    "surprised",
    "thinking",
}
_EMOTION_RE = re.compile(r'"emotion"\s*:\s*"((?:\\.|[^"\\])*)"')
_RESPONSE_START_RE = re.compile(r'"response"\s*:\s*"')


def _decode_complete_json_string(raw: str) -> str | None:
    try:
        value = json.loads(f'"{raw}"')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, str) else None


def _decode_prefix(raw: str, start: int) -> tuple[str, bool]:
    """Decode the safe prefix of one JSON string value.

    Returns ``(decoded, closed)``. Incomplete escapes are held back until a later
    chunk. Malformed escapes stop emission rather than leaking JSON scaffolding.
    """

    out: list[str] = []
    i = start
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            return "".join(out), True
        if ch != "\\":
            # Raw control characters are invalid JSON. Stop rather than speaking
            # data from a malformed structure whose boundaries are uncertain.
            if ord(ch) < 0x20:
                return "".join(out), False
            out.append(ch)
            i += 1
            continue

        if i + 1 >= len(raw):
            break
        esc = raw[i + 1]
        simple = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        if esc in simple:
            out.append(simple[esc])
            i += 2
            continue
        if esc == "u":
            if i + 6 > len(raw):
                break
            digits = raw[i + 2 : i + 6]
            if not re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                return "".join(out), False
            codepoint = int(digits, 16)
            i += 6
            # Handle a surrogate pair only when both halves are available.
            if 0xD800 <= codepoint <= 0xDBFF:
                if i + 6 > len(raw):
                    break
                if raw[i : i + 2] != "\\u":
                    return "".join(out), False
                low_digits = raw[i + 2 : i + 6]
                if not re.fullmatch(r"[0-9A-Fa-f]{4}", low_digits):
                    return "".join(out), False
                low = int(low_digits, 16)
                if not 0xDC00 <= low <= 0xDFFF:
                    return "".join(out), False
                codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                i += 6
            elif 0xDC00 <= codepoint <= 0xDFFF:
                return "".join(out), False
            out.append(chr(codepoint))
            continue
        return "".join(out), False
    return "".join(out), False


@dataclass(slots=True)
class EmotionResponseParser:
    """Parse a structured model reply across arbitrary streaming chunks."""

    emotion: str | None = None
    _raw: str = ""
    _response_start: int | None = None
    _emitted: int = 0
    _closed: bool = False

    def _discover_emotion(self) -> str | None:
        if self.emotion is not None:
            return None
        match = _EMOTION_RE.search(self._raw)
        if match is None:
            return None
        decoded = _decode_complete_json_string(match.group(1))
        if decoded is None:
            return None
        normalized = decoded.strip().lower()
        self.emotion = normalized if normalized in _ALLOWED_EMOTIONS else "neutral"
        return self.emotion

    def _discover_response(self) -> None:
        if self._response_start is not None:
            return
        match = _RESPONSE_START_RE.search(self._raw)
        if match is not None:
            self._response_start = match.end()

    def _new_spoken(self) -> str:
        if self._response_start is None:
            return ""
        decoded, closed = _decode_prefix(self._raw, self._response_start)
        if self._emitted > len(decoded):
            # Defensive invariant: reparsing should only grow the decoded prefix.
            self._emitted = len(decoded)
            return ""
        fresh = decoded[self._emitted :]
        self._emitted = len(decoded)
        self._closed = self._closed or closed
        return fresh

    def feed(self, chunk: str) -> tuple[str | None, str]:
        """Feed one raw model chunk and return ``(new_emotion, spoken_text)``."""

        if not isinstance(chunk, str) or not chunk or self._closed:
            return None, ""
        # Bound the parser independently of the model client. Typical replies are
        # tiny; 1 MiB prevents an accidental endless stream from growing memory.
        if len(self._raw) + len(chunk) > 1_000_000:
            self._closed = True
            return None, ""
        self._raw += chunk
        new_emotion = self._discover_emotion()
        self._discover_response()
        return new_emotion, self._new_spoken()

    def flush(self) -> str:
        """Return any final safely decoded response characters, without JSON."""

        if self._closed:
            return ""
        self._discover_emotion()
        self._discover_response()
        return self._new_spoken()


__all__ = ["EmotionResponseParser"]
