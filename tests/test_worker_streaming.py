"""Worker streaming — Claude Code stream-json → live milestones + writable workdir.

No real `claude`: subprocess.Popen is faked to emit canned stream-json lines.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import apps.cli as cli
from core.connectors.coder.builder import parse_result


class _FakePopen:
    """Stands in for a `claude --output-format stream-json` process: its stdout
    yields the canned lines, then the loop ends."""

    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


_STREAM_LINES = [
    json.dumps({"type": "system", "subtype": "init"}) + "\n",
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Building a calculator."},
                    {"type": "tool_use", "name": "Write", "input": {"file_path": "index.html"}},
                ]
            },
        }
    )
    + "\n",
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "npm i", "description": "Install deps"},
                    }
                ]
            },
        }
    )
    + "\n",
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": (
                'Done.\n'
                'SCRAPPY_RESULT: {"ok": true, "summary": "a calc", "open": "index.html"}'
            ),
        }
    )
    + "\n",
]


def test_streaming_emits_milestones_and_returns_final_text(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(list(_STREAM_LINES)))
    out = cli._run_claude_streaming("build a calc", Path("/tmp"), "/bin/bash", 30, seen.append)

    # System init skipped; thinking + notable tool_uses narrated, in order.
    assert seen == ["Building a calculator.", "Writing index.html", "Install deps"]
    # The final text is the result event's text — and it still carries the line
    # coder.build parses, even though stream-json JSON-wrapped everything.
    assert parse_result(out) == {
        "ok": True,
        "summary": "a calc",
        "open": "index.html",
    }


def test_streaming_without_result_event_falls_back_to_raw_tail(monkeypatch):
    # Non-JSON output (e.g. an older CLI ignoring stream-json): no milestones, no
    # result event — but the raw tail is returned so a trailing SCRAPPY_RESULT
    # printed as plain text still survives for parsing.
    lines = ["just some text\n", 'SCRAPPY_RESULT: {"ok": true, "open": ""}\n']
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(lines))
    out = cli._run_claude_streaming("x", Path("/tmp"), "/bin/bash", 30, None)
    assert parse_result(out) == {"ok": True, "open": ""}


def test_run_local_command_routes_claude_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "WORKSPACE", tmp_path / "ws")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(list(_STREAM_LINES)))
    seen: list[str] = []
    out = cli._run_local_command(
        "claude", "build it", 30, "a1", stream=True, progress_cb=seen.append
    )
    assert seen  # milestones flowed through the routing
    assert parse_result(out)["ok"] is True


def test_worker_workdir_uses_writable_subdir(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "WORKSPACE", tmp_path / "ws")
    d = cli._worker_workdir("agent1")
    assert d == tmp_path / "ws" / "agent1"
    assert d.is_dir()
    (d / "proof").write_text("writable")  # actually writable


def test_worker_workdir_falls_back_when_workspace_unwritable(monkeypatch, tmp_path):
    # WORKSPACE is a FILE, so mkdir underneath it fails — we must still hand back
    # a real, writable directory instead of dying on a permission error.
    blocker = tmp_path / "im-a-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(cli, "WORKSPACE", blocker)
    d = cli._worker_workdir("agent1")
    assert d.is_dir()
    assert d != blocker / "agent1"
    (d / "proof").write_text("writable")
