"""A build must leave a trace, and a turn must never end in silence.

Both come from the same transcript: Scrappy built a timer app, then — asked "did
you recently create any software?" — had no memory of it, dispatched Claude Code
to go hunting the filesystem, and replied with nothing at all.
"""

from __future__ import annotations

import json

import numpy as np

from apps.api.routers.chat import _silent_turn_fallback
from core.connectors.base import InvocationContext
from core.connectors.coder.builder import build_memory_line
from core.connectors.coder.connector import CoderConnector

# ── the memory line ───────────────────────────────────────────────────


def test_memory_line_is_self_contained():
    line = build_memory_line("a pomodoro timer", "built a Pomodoro timer web app", "index.html")
    # A memory has to make sense months later with no conversation around it.
    assert "Built software" in line
    assert "Pomodoro" in line
    assert "index.html" in line
    assert line.endswith(".") or line.endswith("'.") or "Mac." in line


def test_memory_line_keeps_the_goal_when_the_summary_omits_it():
    line = build_memory_line("a ctimer app", "created the files and it runs", None)
    assert "ctimer" in line  # otherwise recall by "ctimer" would never match
    assert "created the files" in line


def test_memory_line_does_not_repeat_itself():
    line = build_memory_line("a calculator", "built a calculator", "index.html")
    assert line.lower().count("calculator") == 1


def test_memory_line_survives_empty_pieces():
    assert build_memory_line("", "", None)  # never blank
    assert "timer" in build_memory_line("timer", "", None)


# ── the connector records it ──────────────────────────────────────────


class _FakeEmbedder:
    async def embed(self, text: str):
        return np.ones(4, dtype=np.float32)


class _FakeSemantic:
    def __init__(self) -> None:
        self.written: list[dict] = []

    async def write(self, user_id, kind, content, embedding, *, source="", importance=0.5):
        self.written.append(
            {"kind": kind, "content": content, "source": source, "importance": importance}
        )
        return "mem-id"


def _ctx(semantic=None, embedder=None, user_uuid="u1") -> InvocationContext:
    return InvocationContext(
        session_id="s1", semantic=semantic, embedder=embedder, user_uuid=user_uuid
    )


async def _invoke_build(monkeypatch, result: dict, ctx: InvocationContext):
    from core.connectors.coder import connector as mod

    async def fake_run_build(goal, *, bridge, session_id=None, on_progress=None):
        return result

    monkeypatch.setattr(mod, "run_build", fake_run_build)
    return await CoderConnector().invoke("build", {"goal": "a ctimer app"}, ctx)


async def test_successful_build_is_remembered(monkeypatch):
    sem = _FakeSemantic()
    await _invoke_build(
        monkeypatch,
        {"ok": True, "summary": "built a countdown timer", "open_target": "index.html"},
        _ctx(sem, _FakeEmbedder()),
    )
    assert len(sem.written) == 1
    row = sem.written[0]
    assert "countdown timer" in row["content"]
    assert row["source"] == "coder.build"
    assert row["importance"] >= 0.7  # builds are worth recalling months later
    # The kind must be one the chat context actually retrieves.
    from apps.api.routers.chat import _FACT_KINDS

    assert row["kind"] in _FACT_KINDS


async def test_failed_build_is_not_remembered(monkeypatch):
    sem = _FakeSemantic()
    await _invoke_build(
        monkeypatch, {"ok": False, "summary": "couldn't finish"}, _ctx(sem, _FakeEmbedder())
    )
    assert sem.written == []  # don't pollute memory with things that didn't happen


async def test_build_still_succeeds_when_memory_is_unavailable(monkeypatch):
    # A bare context (no memory wired) must not break the build.
    res = await _invoke_build(
        monkeypatch, {"ok": True, "summary": "built it"}, _ctx(None, None, None)
    )
    assert res["ok"] is True


async def test_build_still_succeeds_when_the_memory_write_fails(monkeypatch):
    class _Broken(_FakeSemantic):
        async def write(self, *a, **kw):
            raise RuntimeError("db down")

    res = await _invoke_build(
        monkeypatch,
        {"ok": True, "summary": "built it", "open_target": "index.html"},
        _ctx(_Broken(), _FakeEmbedder()),
    )
    assert res["ok"] is True  # remembering is best-effort, never load-bearing


# ── never a silent turn ───────────────────────────────────────────────


def test_fallback_relays_the_last_tool_result():
    out = _silent_turn_fallback([{"tool": "coder.build", "ok": True}], "built a timer app")
    assert "coder.build" in out and "built a timer app" in out


def test_fallback_reports_a_failed_tool_honestly():
    out = _silent_turn_fallback(
        [{"tool": "coder.code", "ok": False}], "error: worker not connected"
    )
    assert "didn't go through" in out
    assert "worker not connected" in out


def test_fallback_without_any_tool_asks_for_a_retry():
    out = _silent_turn_fallback([], "")
    assert out and "again" in out.lower()


def test_fallback_with_a_tool_but_no_summary():
    out = _silent_turn_fallback([{"tool": "memory.search", "ok": True}], "")
    assert "memory.search" in out


def test_fallback_is_never_blank():
    for steps, summary in (([], ""), ([{"tool": "x"}], ""), ([{"tool": "x"}], "y")):
        assert _silent_turn_fallback(steps, summary).strip()


def test_fallback_clips_a_huge_tool_dump():
    out = _silent_turn_fallback([{"tool": "web.fetch", "ok": True}], json.dumps(["x"] * 500))
    assert len(out) < 300  # a wall of JSON is not an answer
