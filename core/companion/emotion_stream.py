"""Incremental parser for Mini's streamed JSON emotion/response contract.

The model is instructed to emit a single JSON object with `emotion` first and
`response` second. This parser exposes the emotion as soon as it is complete,
then streams only the decoded response string while hiding JSON scaffolding.

It is deliberately small and deterministic: no eval, no model calls, and no
best-effort invention when the stream is malformed.
"""

from __future__ import annotations

import json
import re

_EMOTION_RE = re.compile(r'"emotion"\s*:\s*"((?:\\.|[^"\\])*)"')
_RESPONSE_RE = re.compile(r'"response"\s*:\s*"')

_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class EmotionResponseParser:
    """Parse streamed `{"emotion": ..., "response": ...}` output.

    `feed()` returns `(new_emotion, spoken_text_delta)`.
    `flush()` returns any final response text that could only be recovered after
    the stream ended. `.emotion` always holds the latest parsed emotion.
    """

    def __init__(self) -> None:
        self.emotion: str | None = None
        self._buffer = ""
        self._response_started = False
        self._scan_pos = 0
        self._done = False
        self._escape = False
        self._unicode_digits: str | None = None
        self._fallback_emitted = False

    def feed(self, chunk: str) -> tuple[str | None, str]:
        if not chunk:
            return None, ""

        self._buffer += chunk
        new_emotion: str | None = None

        if self.emotion is None:
            match = _EMOTION_RE.search(self._buffer)
            if match:
                try:
                    self.emotion = json.loads('"' + match.group(1) + '"')
                except json.JSONDecodeError:
                    self.emotion = match.group(1)
                new_emotion = self.emotion

        if self._done:
            return new_emotion, ""

        if not self._response_started:
            match = _RESPONSE_RE.search(self._buffer)
            if not match:
                return new_emotion, ""
            self._response_started = True
            self._scan_pos = match.end()

        spoken: list[str] = []
        i = self._scan_pos
        while i < len(self._buffer) and not self._done:
            ch = self._buffer[i]

            if self._unicode_digits is not None:
                self._unicode_digits += ch
                if len(self._unicode_digits) == 4:
                    digits = self._unicode_digits
                    self._unicode_digits = None
                    try:
                        spoken.append(chr(int(digits, 16)))
                    except ValueError:
                        spoken.append("\\u" + digits)
                i += 1
                continue

            if self._escape:
                self._escape = False
                if ch == "u":
                    self._unicode_digits = ""
                else:
                    spoken.append(_ESCAPE_MAP.get(ch, ch))
                i += 1
                continue

            if ch == "\\":
                self._escape = True
                i += 1
                continue

            if ch == '"':
                self._done = True
                i += 1
                break

            spoken.append(ch)
            i += 1

        self._scan_pos = i
        return new_emotion, "".join(spoken)

    def flush(self) -> str:
        """Finish parsing without fabricating missing structure.

        Normal streaming paths already emitted all response text via `feed()`.
        The fallback exists only for a complete JSON object where response-start
        detection never happened (for example unusual whitespace/escaping).
        """

        if self._response_started or self._fallback_emitted:
            return ""
        try:
            obj = json.loads(self._buffer)
        except (json.JSONDecodeError, TypeError):
            return ""
        if self.emotion is None and isinstance(obj, dict):
            value = obj.get("emotion")
            if isinstance(value, str):
                self.emotion = value
        response = obj.get("response") if isinstance(obj, dict) else None
        if isinstance(response, str):
            self._fallback_emitted = True
            return response
        return ""
