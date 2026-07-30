"""Readable terminal rendering for Scrappy's replies.

The model answers in Markdown ("**bold**", numbered lists, `code`), which reads
as noise in a terminal — and long replies arrive as one unbroken wall of text.
This module turns a live token stream into clean, wrapped, ANSI-styled lines.

Pure and dependency-free (colors are injected, never hard-coded), so every rule
here is unit-testable with plain strings.
"""

from __future__ import annotations

import re

# Inline Markdown we translate to ANSI (bold, `code`, ### headings).
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)(\d{1,2})[.)]\s+(.*)$")

# A run-on numbered list on ONE line: "…tasks.2. **Scalability**: …".
# Requires sentence punctuation before, a 1-2 digit number, then a space and a
# bold/capitalised start — so decimals ("3.5") and years ("Nov 23. 2026") are safe.
_INLINE_ITEM_RE = re.compile(r"(?<=[.:!?])\s*(\d{1,2})[.)]\s+(?=\*\*|[A-Z])")

MIN_WIDTH = 40
MAX_WIDTH = 100


class Palette:
    """ANSI codes to style with. Defaults are empty = plain text (and tests)."""

    def __init__(
        self, *, bold: str = "", dim: str = "", cyan: str = "", green: str = "", reset: str = ""
    ) -> None:
        self.bold, self.dim, self.cyan, self.green, self.reset = bold, dim, cyan, green, reset


PLAIN = Palette()


def split_run_on_lists(text: str) -> str:
    """Break a run-on numbered list onto separate lines.

    The model sometimes emits "1. A…2. B…3. C…" with no newlines at all. Only
    fires when a line holds 2+ such markers, so ordinary prose is left alone.
    """
    out = []
    for line in text.split("\n"):
        markers = _INLINE_ITEM_RE.findall(line)
        # Two inline markers is unambiguously a list. One is enough only when the
        # line already OPENS with a numbered item ("1. …" — whose own marker sits
        # at position 0 and so never matches the mid-line pattern).
        if len(markers) >= 2 or (markers and _NUMBERED_RE.match(line)):
            line = _INLINE_ITEM_RE.sub(lambda m: "\n" + m.group(1) + ". ", line)
        out.append(line)
    return "\n".join(out)


def style_inline(text: str, pal: Palette = PLAIN) -> str:
    """Apply inline Markdown → ANSI (bold, `code`), leaving other text intact."""
    text = _BOLD_RE.sub(lambda m: f"{pal.bold}{m.group(1)}{pal.reset}", text)
    text = _CODE_RE.sub(lambda m: f"{pal.cyan}{m.group(1)}{pal.reset}", text)
    return text


def _visible_len(text: str) -> int:
    """Length ignoring ANSI escapes, so wrapping math stays correct."""
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


def wrap(text: str, width: int, *, indent: str = "", hang: str = "") -> list[str]:
    """Word-wrap to `width`, prefixing `indent` then `hang` on continuation lines.

    ANSI-aware: escape codes don't count toward the visible width.
    """
    words = text.split()
    if not words:
        return [indent.rstrip()] if indent.strip() else [""]
    hang = hang or " " * len(indent)
    lines: list[str] = []
    cur, prefix = "", indent
    for word in words:
        candidate = word if not cur else f"{cur} {word}"
        if cur and _visible_len(prefix + candidate) > width:
            lines.append(prefix + cur)
            cur, prefix = word, hang
        else:
            cur = candidate
    lines.append(prefix + cur)
    return lines


def render_line(line: str, width: int, pal: Palette = PLAIN, *, indent: str = "  ") -> list[str]:
    """Render ONE logical line of Markdown into wrapped, styled terminal lines."""
    if not line.strip():
        return [""]

    heading = _HEADING_RE.match(line)
    if heading:
        body = style_inline(heading.group(2).strip(), pal)
        return wrap(f"{pal.bold}{body}{pal.reset}", width, indent=indent)

    numbered = _NUMBERED_RE.match(line)
    if numbered:
        lead, num, body = numbered.groups()
        marker = f"{indent}{lead}{pal.green}{num}.{pal.reset} "
        return wrap(
            style_inline(body, pal), width, indent=marker, hang=" " * _visible_len(marker)
        )

    bullet = _BULLET_RE.match(line)
    if bullet:
        lead, body = bullet.groups()
        marker = f"{indent}{lead}{pal.green}•{pal.reset} "
        return wrap(
            style_inline(body, pal), width, indent=marker, hang=" " * _visible_len(marker)
        )

    return wrap(style_inline(line.strip(), pal), width, indent=indent)


def render_block(text: str, width: int, pal: Palette = PLAIN, *, indent: str = "  ") -> list[str]:
    """Render a whole reply: fix run-on lists, then render each line."""
    lines: list[str] = []
    for line in split_run_on_lists(text).split("\n"):
        lines.extend(render_line(line, width, pal, indent=indent))
    return lines


def clamp_width(cols: int) -> int:
    """Keep line length in a comfortable reading range regardless of terminal size."""
    return max(MIN_WIDTH, min(cols - 2, MAX_WIDTH))


class ReplyRenderer:
    """Turns a live token stream into finished, printable lines.

    Emits a line as soon as it's complete (a newline arrived) or once it grows
    past the wrap width — so output still streams as the model writes, instead of
    waiting for the whole reply, but is never a jammed-together wall of text.
    """

    # Only start emitting a newline-less paragraph once it's this many lines long
    # (see `_drain` — draining sooner would break run-on list detection).
    DRAIN_AFTER = 3

    def __init__(self, width: int, pal: Palette = PLAIN, *, indent: str = "  ") -> None:
        self.width, self.pal, self.indent = width, pal, indent
        self._buf = ""  # RAW (unrendered) text of the line still being written
        self._cont: str | None = None  # continuation indent once a line has spilled
        self.wrote_any = False

    def feed(self, chunk: str) -> list[str]:
        """Absorb streamed text; return whatever lines are now complete."""
        # Turn run-on numbered lists into REAL newlines as they stream in.
        # Idempotent: a split item's marker moves to column 0, where the mid-line
        # pattern no longer matches it.
        self._buf = split_run_on_lists(self._buf + chunk)
        out: list[str] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            out.extend(self._emit(line))
            self._cont = None  # a new logical line starts fresh
        out.extend(self._drain())
        return out

    def flush(self) -> list[str]:
        """Emit whatever's left at the end of the stream."""
        rest, self._buf = self._buf, ""
        if not rest.strip():
            return []
        out = self._emit(rest)
        self._cont = None
        return out

    def _prefix(self, line: str) -> tuple[str, str]:
        """(printed prefix, raw body) for a logical line, and the marker it owns.

        Once a line has already spilled we reuse its hanging indent, so the rest
        of a long list item stays aligned under the item text instead of drifting
        back to the left margin.
        """
        if self._cont is not None:
            return self._cont, line
        numbered = _NUMBERED_RE.match(line)
        if numbered:
            lead, num, body = numbered.groups()
            return f"{self.indent}{lead}{self.pal.green}{num}.{self.pal.reset} ", body
        bullet = _BULLET_RE.match(line)
        if bullet:
            lead, body = bullet.groups()
            return f"{self.indent}{lead}{self.pal.green}•{self.pal.reset} ", body
        return self.indent, line

    def _drain(self) -> list[str]:
        """Emit visual lines from a long paragraph that has no newline yet.

        Deliberately LAZY — it waits until the buffer is several lines long before
        emitting anything. Draining word-by-word as tokens land would consume the
        text before a run-on list's later markers ("…tasks.2. **Scale**") arrive,
        and `split_run_on_lists` would never get to break it into items. This only
        exists so a genuinely long prose paragraph still streams instead of
        landing all at once.
        """
        out: list[str] = []
        while _visible_len(self._buf) > self.DRAIN_AFTER * self.width:
            prefix, body = self._prefix(self._buf)
            trailing = " " if body[-1:].isspace() else ""  # keep the word boundary
            words = body.split()
            if len(words) < 2:
                break
            line, taken = "", 0
            for word in words:
                candidate = word if not line else f"{line} {word}"
                if _visible_len(prefix + style_inline(candidate, self.pal)) > self.width:
                    break
                line, taken = candidate, taken + 1
            if taken == 0 or taken >= len(words):
                break  # nothing fits yet, or it all fits — wait for more
            out.append(prefix + style_inline(line, self.pal))
            self.wrote_any = True
            self._cont = " " * _visible_len(prefix)
            self._buf = " ".join(words[taken:]) + trailing
        return out

    def _emit(self, line: str) -> list[str]:
        if self._cont is not None:  # tail of a line that already spilled
            if not line.strip():
                return []
            rendered = wrap(
                style_inline(line.strip(), self.pal), self.width,
                indent=self._cont, hang=self._cont,
            )
        else:
            rendered = render_block(line, self.width, self.pal, indent=self.indent)
        if any(x.strip() for x in rendered):
            self.wrote_any = True
        return rendered


# ── tool activity lines ───────────────────────────────────────────────

_MAX_SUMMARY = 70


def summarize_tool_result(tool: str, raw: str) -> str:
    """One human line for a finished tool — never a raw JSON dump.

    Tool results arrive as JSON blobs that, printed straight, truncate mid-string
    into unreadable noise. We report the shape (how many rows) and a clean
    snippet of the first item instead.
    """
    raw = (raw or "").strip()
    if not raw:
        return tool

    import json

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if isinstance(parsed, list):
        n = len(parsed)
        head = parsed[0] if n else None
        if isinstance(head, dict):
            snippet = str(head.get("content") or head.get("summary") or head.get("text") or "")
        else:
            snippet = str(head or "")
        count = f"{n} result{'s' if n != 1 else ''}"
        snippet = " ".join(snippet.split())
        if snippet:
            return f"{tool} · {count} · {_clip(snippet, _MAX_SUMMARY)}"
        return f"{tool} · {count}"

    if isinstance(parsed, dict):
        snippet = str(
            parsed.get("summary") or parsed.get("content") or parsed.get("message") or ""
        )
        snippet = " ".join(snippet.split())
        if snippet:
            return f"{tool} · {_clip(snippet, _MAX_SUMMARY)}"
        return tool

    return f"{tool} · {_clip(' '.join(raw.split()), _MAX_SUMMARY)}"


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap].rstrip() + "…"
