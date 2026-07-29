"""IM-4.1 / IM-4.3 — phrase streaming + tool narration (pure, no audio)."""

from __future__ import annotations

from core.voice.narration import (
    narrate_approval,
    narrate_progress,
    narrate_tool,
    pop_phrase,
)

# ── pop_phrase (IM-4.1) ───────────────────────────────────────────────


def test_pop_phrase_waits_for_enough_text():
    # Too short / no break yet → nothing to speak.
    assert pop_phrase("Hi. ") == (None, "Hi. ")
    assert pop_phrase("still going") == (None, "still going")


def test_pop_phrase_pops_at_natural_break():
    phrase, rest = pop_phrase("This is a full sentence. Next bit")
    assert phrase == "This is a full sentence."
    assert rest == "Next bit"


def test_pop_phrase_skips_tiny_fragments():
    # The early comma break (< MIN_CHUNK_CHARS) is skipped; it pops at the later "?".
    phrase, rest = pop_phrase("Hey, how are you doing? more")
    assert phrase == "Hey, how are you doing?"
    assert rest == "more"


def test_pop_phrase_drains_a_stream():
    text = "Okay, let me think about this. First I will search. Then I will reply. "
    buf, out = "", []
    for ch in text:  # simulate token-by-token arrival
        buf += ch
        while True:
            phrase, buf = pop_phrase(buf)
            if phrase is None:
                break
            out.append(phrase)
    joined = " ".join(out) + buf.strip()
    assert "search." in " ".join(out)
    assert joined.replace("  ", " ").startswith("Okay, let me think about this.")


# ── narrate_tool (IM-4.3) ─────────────────────────────────────────────


def test_narrate_tool_exact_and_namespace():
    assert narrate_tool("memory.search") == "Checking my memory."
    assert narrate_tool("memory.forget") == "Checking my memory."  # namespace fallback
    assert narrate_tool("coder.code") == "Getting Claude on it."


def test_narrate_tool_self_modification_is_voiced():
    assert narrate_tool("self.describe") == "Looking at my own code."
    assert narrate_tool("self.propose_change") == "Drafting a change to myself."
    assert narrate_tool("self.apply_change") == "Applying the change to myself."


def test_narrate_tool_expert_fallback():
    assert narrate_tool("ask_engineer") == "On it."  # explicit
    assert narrate_tool("ask_designer") == "Asking my designer."  # generic ask_ fallback


def test_narrate_tool_build_is_voiced():
    assert narrate_tool("coder.build") == "On it — building that now."


# ── narrate_progress (streaming-progress v2) ──────────────────────────


def test_narrate_progress_passes_through_milestones():
    assert narrate_progress("Writing index.html") == "Writing index.html"
    assert narrate_progress("  Running:   npm install  ") == "Running: npm install"


def test_narrate_progress_ignores_empty_noise():
    assert narrate_progress("") is None
    assert narrate_progress("   ") is None
    assert narrate_progress("x") is None  # single char isn't worth voicing


def test_narrate_progress_clips_long_lines():
    out = narrate_progress("word " * 100)
    assert out is not None and out.endswith("…") and len(out) <= 201


def test_narrate_tool_unknown_is_silent():
    assert narrate_tool("weird.thing") is None
    assert narrate_tool("") is None


# ── narrate_approval (IM-4.3) ─────────────────────────────────────────


def test_narrate_approval_friendly():
    assert "send that email" in narrate_approval("gmail.send")
    assert "apply that change to my own code" in narrate_approval("self.apply_change")


def test_narrate_approval_namespace_and_fallback():
    assert "send that email" in narrate_approval("gmail.reply")  # gmail namespace
    assert "change my own code" in narrate_approval("self.something")
    assert "run foo.bar" in narrate_approval("foo.bar")  # generic
