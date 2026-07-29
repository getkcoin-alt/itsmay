"""coder.build — monitored Claude Code build → structured report → auto-open.

Offline: the worker bridge is faked (returns a canned Claude Code transcript with
a SCRAPPY_RESULT line); no real Mac, no real claude.
"""

from __future__ import annotations

import json

from core.connectors.coder.builder import (
    _safe_target,
    build_prompt,
    parse_result,
    run_build,
)
from core.connectors.registry import get_registry


class FakeBridge:
    def __init__(
        self,
        build_output: str = "",
        online: bool = True,
        milestones: list[str] | None = None,
    ) -> None:
        self._build = build_output
        self._online = online
        self._milestones = milestones or []
        self.calls: list[dict] = []

    def worker_online(self) -> bool:
        return self._online

    async def submit(
        self, *, agent_id, kind, cmd, timeout, task="",  # noqa: ASYNC109 - mirrors bridge API
        stream_progress=False, on_progress=None,
    ):
        self.calls.append(
            {"kind": kind, "cmd": cmd, "timeout": timeout, "stream_progress": stream_progress}
        )
        if kind == "claude":
            # Model the streaming worker POSTing live milestones as it builds.
            if on_progress:
                for m in self._milestones:
                    on_progress(m)
            return self._build
        return ""


def _report(ok=True, summary="built a calculator", open_="index.html") -> str:
    return json.dumps({"ok": ok, "summary": summary, "open": open_})


def _transcript(report_json: str, prefix: str = "Setting it up...\n") -> str:
    return f"{prefix}SCRAPPY_RESULT: {report_json}"


# ── pure units ────────────────────────────────────────────────────────


def test_build_prompt_asks_for_structured_result():
    p = build_prompt("a calculator web app")
    assert "a calculator web app" in p
    assert "SCRAPPY_RESULT" in p
    assert '"ok"' in p and '"open"' in p
    assert "COMPLETE" in p.upper() and "autonomously" in p


def test_parse_result_variants():
    assert parse_result(_transcript(_report())) == {
        "ok": True,
        "summary": "built a calculator",
        "open": "index.html",
    }
    assert parse_result("no result line here") is None
    assert parse_result('SCRAPPY_RESULT: {"no_ok": 1}') is None
    # Last valid line wins.
    two = 'SCRAPPY_RESULT: {"ok": false, "open": ""}\nSCRAPPY_RESULT: {"ok": true, "open": "a"}'
    assert parse_result(two)["ok"] is True


def test_safe_target():
    assert _safe_target("index.html")
    assert _safe_target("MyApp.app")
    assert not _safe_target("")
    assert not _safe_target("index.html; rm -rf ~")  # shell metachars
    assert not _safe_target("a" * 400)


# ── run_build orchestration ───────────────────────────────────────────


async def test_build_happy_path_reports_and_opens():
    bridge = FakeBridge(_transcript(_report(open_="index.html")))
    res = await run_build("a calculator", bridge=bridge, session_id="sess1234")

    assert res["ok"] is True
    assert res["opened"] is True
    assert res["summary"] == "built a calculator"
    assert res["open_target"] == "index.html"
    # Two worker round-trips: the build (claude), then the open (bash).
    assert [c["kind"] for c in bridge.calls] == ["claude", "bash"]
    assert bridge.calls[1]["cmd"] == "open index.html"


async def test_build_streams_progress_then_opens():
    seen: list[str] = []
    bridge = FakeBridge(
        _transcript(_report(open_="index.html")),
        milestones=["Writing index.html", "Running: npm install"],
    )
    res = await run_build("a calc", bridge=bridge, session_id="s", on_progress=seen.append)

    assert res["ok"] is True and res["opened"] is True
    # The worker's streamed play-by-play, then our own "Opening …", in order.
    assert seen == ["Writing index.html", "Running: npm install", "Opening index.html"]
    # The build round-trip opted into streaming; the open call did not.
    build_call = next(c for c in bridge.calls if c["kind"] == "claude")
    assert build_call["stream_progress"] is True


async def test_build_without_sink_still_builds():
    # No on_progress wired (e.g. text CLI) — milestones are simply dropped.
    bridge = FakeBridge(_transcript(_report(open_="index.html")), milestones=["x", "y"])
    res = await run_build("a calc", bridge=bridge)
    assert res["ok"] is True and res["opened"] is True


async def test_build_ok_but_nothing_to_open():
    bridge = FakeBridge(_transcript(_report(open_="")))
    res = await run_build("a library", bridge=bridge)
    assert res["ok"] is True and res["opened"] is False
    assert [c["kind"] for c in bridge.calls] == ["claude"]  # no open call


async def test_build_refuses_unsafe_open_target():
    bridge = FakeBridge(_transcript(_report(open_="x.html; rm -rf ~")))
    res = await run_build("sneaky", bridge=bridge)
    assert res["ok"] is True and res["opened"] is False
    assert len(bridge.calls) == 1  # never ran the unsafe open


async def test_build_not_finished():
    bridge = FakeBridge(_transcript(_report(ok=False, summary="missing a key", open_="")))
    res = await run_build("half thing", bridge=bridge)
    assert res["ok"] is False and res["opened"] is False


async def test_build_needs_worker():
    bridge = FakeBridge(online=False)
    res = await run_build("anything", bridge=bridge)
    assert res["ok"] is False and res.get("needs_worker") is True
    assert bridge.calls == []


async def test_build_empty_goal():
    bridge = FakeBridge()
    res = await run_build("   ", bridge=bridge)
    assert res["ok"] is False
    assert bridge.calls == []


async def test_build_unparseable_output():
    bridge = FakeBridge("I did some stuff but forgot the result line")
    res = await run_build("do it", bridge=bridge)
    assert res["ok"] is False
    assert res.get("raw")
    assert [c["kind"] for c in bridge.calls] == ["claude"]


# ── connector wiring ──────────────────────────────────────────────────


def test_build_tool_registered():
    tool = get_registry().get_tool("coder.build")
    assert tool is not None
    assert tool.spec.executor == "server"
    assert "app.open" in tool.spec.side_effects
