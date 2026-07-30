"""Terminal rendering + SSE framing — readable replies in the CLI.

The reply text is Markdown; the terminal isn't. These are the rules that turn a
live token stream into something a human can actually read.
"""

from __future__ import annotations

import pytest

from apps.cli import render
from apps.cli.render import (
    Palette,
    ReplyRenderer,
    render_block,
    render_line,
    split_run_on_lists,
    style_inline,
    summarize_tool_result,
    wrap,
)

ANSI = Palette(bold="<b>", dim="<d>", cyan="<c>", green="<g>", reset="<r>")


# ── run-on numbered lists ─────────────────────────────────────────────


def test_split_run_on_list_breaks_jammed_items():
    # Exactly what the terminal showed: items glued together with no newlines.
    text = "1. **Automation**: I automate things.2. **Scalability**: I scale things."
    out = split_run_on_lists(text)
    assert out.splitlines() == [
        "1. **Automation**: I automate things.",
        "2. **Scalability**: I scale things.",
    ]


def test_split_run_on_list_leaves_prose_alone():
    # Single marker isn't a list — and decimals/years must never be split.
    assert split_run_on_lists("Ship it. 2. maybe") == "Ship it. 2. maybe"
    assert split_run_on_lists("It costs 3.5 units. Nov 23. 2026 is the date.") == (
        "It costs 3.5 units. Nov 23. 2026 is the date."
    )


# ── inline markdown ───────────────────────────────────────────────────


def test_style_inline_bold_and_code():
    assert style_inline("**hi** and `ls -la`", ANSI) == "<b>hi<r> and <c>ls -la<r>"


def test_style_inline_plain_by_default():
    assert style_inline("**hi** and `code`") == "hi and code"


# ── wrapping ──────────────────────────────────────────────────────────


def test_wrap_breaks_at_width_with_hanging_indent():
    lines = wrap("aaa bbb ccc ddd", 11, indent="  ", hang="    ")
    assert lines == ["  aaa bbb", "    ccc ddd"]


def test_wrap_ignores_ansi_in_width_math():
    # Real escape codes are invisible, so this fits on one line (visible "aaa bbb"
    # is 7 chars) even though the raw string is far longer than the width.
    styled = "\033[1maaa bbb\033[0m"
    assert wrap(styled, 12, indent="") == [styled]


# ── line rendering ────────────────────────────────────────────────────


def test_render_numbered_item_keeps_marker_and_indents_wrap():
    lines = render_line("1. **Automation**: it runs itself", 30, indent="  ")
    assert lines[0].startswith("  1. Automation")
    assert all(ln.startswith("  ") for ln in lines)


def test_render_bullet_becomes_dot():
    assert render_line("- do the thing", 40)[0] == "  • do the thing"


def test_render_heading_is_bolded():
    assert render_line("## Plan", 40, ANSI) == ["  <b>Plan<r>"]


def test_render_blank_line_preserved():
    assert render_line("   ", 40) == [""]


def test_render_block_splits_and_renders_jammed_list():
    out = render_block("1. **A**: first.2. **B**: second.", 60)
    assert out == ["  1. A: first.", "  2. B: second."]


# ── streaming renderer ────────────────────────────────────────────────


def test_streamed_tokens_emit_completed_lines():
    r = ReplyRenderer(60)
    assert r.feed("Hello Boss.") == []  # nothing complete yet
    assert r.feed("\nSecond line.\n") == ["  Hello Boss.", "  Second line."]
    assert r.flush() == []


def test_stream_flush_emits_trailing_partial_line():
    r = ReplyRenderer(60)
    r.feed("no trailing newline")
    assert r.flush() == ["  no trailing newline"]
    assert r.wrote_any is True


def test_stream_holds_short_paragraph_until_complete():
    # Deliberately lazy: a paragraph shorter than DRAIN_AFTER lines is held, so a
    # run-on list's later markers can still arrive and be split into items.
    r = ReplyRenderer(24)
    assert r.feed("word " * 8) == []


def test_stream_wraps_long_paragraph_while_streaming():
    # Past the threshold it does stream, so a genuinely long answer isn't silent.
    r = ReplyRenderer(24)
    emitted = r.feed("word " * 40)
    assert emitted
    assert all(len(ln) <= 24 for ln in emitted)


def _stream(text: str, width: int = 60, seed: int = 0, pal: Palette = render.PLAIN) -> list[str]:
    """Feed `text` through the renderer in randomly-sized chunks, like real tokens."""
    import random

    rng = random.Random(seed)
    r = ReplyRenderer(width, pal)
    out, i = [], 0
    while i < len(text):
        n = rng.randint(1, 14)
        out.extend(r.feed(text[i : i + n]))
        i += n
    out.extend(r.flush())
    return out


def _words(lines: list[str]) -> list[str]:
    import re as _re

    plain = " ".join(_re.sub(r"\033\[[0-9;]*m", "", ln) for ln in lines)
    return plain.split()


@pytest.mark.parametrize("seed", range(6))
def test_streaming_never_merges_or_drops_words(seed):
    # Chunk boundaries must not eat the space between words ("analyze"+"large"
    # → "analyzelarge") nor lose any text, whatever the token split looks like.
    text = (
        "I can process and analyze large amounts of data, generate insights, "
        "and make predictions using models that keep improving over time."
    )
    assert _words(_stream(text, width=48, seed=seed)) == text.split()


@pytest.mark.parametrize("seed", range(4))
def test_streaming_splits_run_on_list_regardless_of_chunking(seed):
    # The exact shape from the terminal: items glued together, arriving in
    # arbitrary token chunks. Each item must still land on its own line.
    text = (
        "My superpowers, Boss: 1. **Automation**: I automate tasks."
        "2. **Scalability**: I scale systems.3. **Memory**: I remember things."
    )
    lines = [ln for ln in _stream(text, width=70, seed=seed) if ln.strip()]
    starts = [ln.strip()[:2] for ln in lines]
    assert "1." in starts and "2." in starts and "3." in starts
    assert _words(lines)[-1] == "things."  # nothing lost


def test_streamed_list_item_keeps_hanging_indent_when_it_wraps():
    long_item = "1. **Automation**: " + "word " * 30
    lines = [ln for ln in _stream(long_item, width=40, seed=1) if ln.strip()]
    assert lines[0].startswith("  1. ")
    # Continuation lines align under the item text, not back at the margin.
    assert all(ln.startswith("     ") for ln in lines[1:])


def test_stream_wrote_any_false_for_empty_reply():
    r = ReplyRenderer(60)
    r.feed("")
    assert r.flush() == []
    assert r.wrote_any is False  # drives the "(no reply)" hint


# ── tool result summaries ─────────────────────────────────────────────


def test_summarize_list_result_reports_count_and_snippet():
    raw = '[{"content": "Communication preference: direct, sharp"}, {"content": "x"}]'
    out = summarize_tool_result("memory.search", raw)
    assert out.startswith("memory.search · 2 results · Communication preference")
    assert "{" not in out  # never a raw JSON dump


def test_summarize_singular_and_dict_and_plain():
    assert summarize_tool_result("memory.search", "[]") == "memory.search · 0 results"
    assert summarize_tool_result("t", '[{"content":"a"}]').startswith("t · 1 result ·")
    built = summarize_tool_result("coder.build", '{"summary": "built it"}')
    assert built == "coder.build · built it"
    assert summarize_tool_result("x", "plain text") == "x · plain text"


def test_summarize_clips_long_output():
    out = summarize_tool_result("t", "y" * 500)
    assert len(out) < 120 and out.endswith("…")


def test_clamp_width_bounds():
    assert render.clamp_width(20) == render.MIN_WIDTH
    assert render.clamp_width(500) == render.MAX_WIDTH
    assert render.clamp_width(82) == 80


# ── SSE framing (the newline-eating bug) ──────────────────────────────


class _FakeResp:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


async def _collect(lines):
    from apps.cli import _sse_events

    return [ev async for ev in _sse_events(_FakeResp(lines))]


@pytest.mark.asyncio
async def test_sse_rejoins_multiline_data():
    # A multi-line token is split across data: lines by the server; rejoining
    # them with "\n" is what keeps Scrappy's lists from collapsing into a wall.
    events = await _collect(["event: token", "data: line one", "data: line two", ""])
    assert events == [("token", "line one\nline two")]


@pytest.mark.asyncio
async def test_sse_preserves_leading_space_in_token():
    events = await _collect(["event: token", "data:  indented", ""])
    assert events == [("token", " indented")]


@pytest.mark.asyncio
async def test_sse_separates_frames_and_skips_comments():
    events = await _collect(
        [": keep-alive", "event: token", "data: a", "", "event: done", "data: {}", ""]
    )
    assert events == [("token", "a"), ("done", "{}")]


@pytest.mark.asyncio
async def test_sse_flushes_unterminated_final_frame():
    events = await _collect(["event: done", "data: {}"])  # no trailing blank line
    assert events == [("done", "{}")]
