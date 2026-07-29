"""One Claude Code session — follow-ups go to the same window, not a new one."""

from __future__ import annotations

import importlib.util
import sys
import types
from types import SimpleNamespace

import pytest

# voice_loop imports sounddevice (mac-only); stub it so this runs in CI.
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
_spec = importlib.util.spec_from_file_location(
    "vl_under_test", "apps/mac_agent/voice_loop.py"
)
vl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vl)


def _fake_osascript(state):
    """Route osascript calls by script content; record what happened."""

    def fake(cmd, **_kw):
        script = cmd[2] if len(cmd) >= 3 else ""
        r = SimpleNamespace(stdout="", stderr="")
        if "exists window id" in script:
            r.stdout = "true" if state["window_open"] else "false"
        elif "return id of front window" in script:
            state["opened"] += 1
            r.stdout = state["new_window_id"]
        elif "in window id" in script:
            state["sent"] += 1
        return r

    return fake


@pytest.fixture
def session_reset():
    vl._claude_session["window_id"] = None
    yield
    vl._claude_session["window_id"] = None


def test_first_call_opens_and_remembers_window(session_reset):
    state = {"opened": 0, "sent": 0, "window_open": False, "new_window_id": "24293"}
    vl._run_silent = _fake_osascript(state)
    out = vl.execute_mac_tool("mac.claude_code", {"prompt": "build a calculator"})
    assert "started a Claude Code session" in out
    assert vl._claude_session["window_id"] == "24293"
    assert state["opened"] == 1 and state["sent"] == 0


def test_followup_reuses_same_session(session_reset):
    vl._claude_session["window_id"] = "24293"
    state = {"opened": 0, "sent": 0, "window_open": True, "new_window_id": "X"}
    vl._run_silent = _fake_osascript(state)
    out = vl.execute_mac_tool("mac.claude_code", {"prompt": "now add a GUI"})
    assert "sent to the open Claude Code session" in out
    assert state["sent"] == 1
    assert state["opened"] == 0  # did NOT open a second window


def test_reopens_when_window_was_closed(session_reset):
    vl._claude_session["window_id"] = "99999"  # stale — user closed it
    state = {"opened": 0, "sent": 0, "window_open": False, "new_window_id": "55555"}
    vl._run_silent = _fake_osascript(state)
    out = vl.execute_mac_tool("mac.claude_code", {"prompt": "keep going"})
    assert "started a Claude Code session" in out
    assert state["opened"] == 1 and state["sent"] == 0
    assert vl._claude_session["window_id"] == "55555"


def test_claude_flags_default_is_autonomous(monkeypatch):
    # Unset → autonomous, so Scrappy-driven Claude Code doesn't stop for approvals.
    monkeypatch.delenv("SCRAPPY_CLAUDE_FLAGS", raising=False)
    assert vl._claude_flags() == "--dangerously-skip-permissions"


def test_claude_flags_respects_override(monkeypatch):
    monkeypatch.setenv("SCRAPPY_CLAUDE_FLAGS", "--permission-mode acceptEdits")
    assert vl._claude_flags() == "--permission-mode acceptEdits"
