"""Incrementally parse Mini's structured emotion + spoken-response JSON.

The web companion asks the model to emit::

    {"emotion":"happy","response":"Hello there"}

LLM chunks can split anywhere — even in the middle of a JSON escape — so this
parser never calls ``json.loads`` on an incomplete document. It discovers the
emotion once its string closes and emits only newly decoded characters from the
``response`` string as they become safe to decode.

This module intentionally has no model/runtime dependencies; it is a tiny stream
codec used by ``apps.api.routers.mini``.
"""

from __future__ import annotations

import json
import re

_EMOTION_RE = re.compile(r'"emotion"\s*:\s*"((?:\\.|[^"\\])*)"')
_RESPONSE_KEY_RE = re.compile(r'"response"\s*:\s*"')


class EmotionResponseParser:
    """Incremental parser for the companion's JSON response envelope."""

    def __init__(self) -> None:
        self._buffer = ""
        self._response_cursor: int | None = None
        self._response_closed = False
        self._fallback_emitted = False
        self.emotion: str | None = None

    @staticmethod
    def _decode_json_fragment(fragment: str) -> str:
        """Decode the contents of one JSON string fragment."""
        return json.loads(f'"{fragment}"')

    def _discover_emotion(self) -> str | None:
        if self.emotion is not None:
            return None
        match = _EMOTION_RE.search(self._buffer)
        if match is None:
            return None
        try:
            value = self._decode_json_fragment(match.group(1)).strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if value:
            self.emotion = value
            return value
        return None

    def _ensure_response_cursor(self) -> None:
        if self._response_cursor is not None:
            return
        match = _RESPONSE_KEY_RE.search(self._buffer)
        if match is not None:
            self._response_cursor = match.end()

    def _consume_response(self) -> str:
        self._ensure_response_cursor()
        if self._response_cursor is None or self._response_closed:
            return ""

        out: list[str] = []
        i = self._response_cursor
        n = len(self._buffer)

        while i < n:
            ch = self._buffer[i]
            if ch == '"':
                self._response_closed = True
                i += 1
                break
            if ch != "\\":
                out.append(ch)
                i += 1
                continue

            # An escape can be split across model chunks. Leave the cursor on the
            # backslash until the full escape is present, then consume it once.
            if i + 1 >= n:
                break
            esc = self._buffer[i + 1]
            if esc == "u":
                if i + 6 > n:
                    break
                token = self._buffer[i : i + 6]
                try:
                    codepoint = int(token[2:], 16)
                except ValueError:
                    # Invalid model JSON: preserve the literal slash and advance;
                    # flush() still has a chance to provide a readable fallback.
                    out.append("\\")
                    i += 1
                    continue

                # A high surrogate must be paired with the following low surrogate
                # before json.loads can safely turn it into one Unicode character.
                if 0xD800 <= codepoint <= 0xDBFF:
                    if i + 12 > n:
                        break
                    second = self._buffer[i + 6 : i + 12]
                    if not second.startswith("\\u"):
                        out.append("�")
                        i += 6
                        continue
                    try:
                        decoded = self._decode_json_fragment(token + second)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        out.append("�")
                        i += 6
                        continue
                    out.append(decoded)
                    i += 12
                    continue

                try:
                    out.append(self._decode_json_fragment(token))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    out.append("�")
                i += 6
                continue

            escapes = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            decoded = escapes.get(esc)
            if decoded is None:
                # Keep malformed output readable rather than dropping characters.
                out.append(esc)
            else:
                out.append(decoded)
            i += 2

        self._response_cursor = i
        return "".join(out)

    def feed(self, delta: str) -> tuple[str | None, str]:
        """Consume one model delta and return ``(new_emotion, spoken_delta)``."""
        if not delta:
            return None, ""
        self._buffer += delta
        emotion = self._discover_emotion()
        spoken = self._consume_response()
        return emotion, spoken

    def flush(self) -> str:
        """Return a best-effort tail if the model did not honor the JSON contract.

        Valid response text is emitted progressively by ``feed`` and therefore
        returns an empty tail here. For malformed/non-JSON output we return the raw
        text once, preventing a silent companion turn.
        """
        spoken = self._consume_response()
        if spoken:
            return spoken
        if self._response_cursor is not None:
            return ""
        if self._fallback_emitted:
            return ""

        raw = self._buffer.strip()
        if not raw:
            return ""
        self._fallback_emitted = True

        # If a complete JSON object exists but uses an unexpected key order, still
        # prefer its response value over exposing the envelope to the user.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("response")
            if isinstance(value, str):
                if self.emotion is None and isinstance(parsed.get("emotion"), str):
                    self.emotion = parsed["emotion"]
                return value
        return raw
