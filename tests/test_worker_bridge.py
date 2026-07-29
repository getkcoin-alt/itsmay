"""Tests for the local-worker bridge, worker endpoints, and agent dispatch."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from core.connectors.base import InvocationContext
from core.connectors.coder import connector as coder_mod
from core.connectors.coder.connector import CoderConnector
from core.terminal_agent import agent as agent_mod
from core.terminal_agent.agent import TerminalAgent, _AgentToolRouter
from core.worker.bridge import WorkerBridge

# ── WorkerBridge unit tests ───────────────────────────────────────

async def test_bridge_roundtrip():
    bridge = WorkerBridge()

    async def fake_worker():
        cmd = await bridge.next_command(wait=2.0)
        assert cmd is not None
        assert cmd.cmd == "echo hi"
        assert cmd.kind == "bash"
        bridge.complete(cmd.id, "hello-output")

    task = asyncio.create_task(fake_worker())
    out = await bridge.submit(agent_id="a1", kind="bash", cmd="echo hi", timeout=2)
    assert out == "hello-output"
    await task


async def test_next_command_idle_returns_none_and_marks_online():
    bridge = WorkerBridge()
    assert bridge.worker_online() is False
    cmd = await bridge.next_command(wait=0.05)
    assert cmd is None
    # Polling counts as presence.
    assert bridge.worker_online() is True


def test_touch_marks_online():
    bridge = WorkerBridge()
    assert bridge.worker_online() is False
    assert bridge.seconds_since_seen() is None
    bridge.touch()
    assert bridge.worker_online() is True
    assert bridge.seconds_since_seen() is not None


def test_complete_unknown_command_returns_false():
    bridge = WorkerBridge()
    assert bridge.complete("does-not-exist", "x") is False


async def test_bridge_progress_routes_live_milestones_to_sink():
    bridge = WorkerBridge()
    seen: list[str] = []

    async def fake_worker():
        cmd = await bridge.next_command(wait=2.0)
        assert cmd is not None
        # Live milestones stream while the command runs...
        assert bridge.progress(cmd.id, "writing index.html") is True
        assert bridge.progress(cmd.id, "running npm install") is True
        bridge.complete(cmd.id, "done")
        # ...but never after it has resolved.
        assert bridge.progress(cmd.id, "too late") is False

    task = asyncio.create_task(fake_worker())
    out = await bridge.submit(
        agent_id="a1", kind="claude", cmd="build", timeout=2, on_progress=seen.append
    )
    assert out == "done"
    await task
    assert seen == ["writing index.html", "running npm install"]


async def test_bridge_progress_unknown_or_sinkless_is_false():
    bridge = WorkerBridge()
    assert bridge.progress("does-not-exist", "x") is False  # unknown command

    async def fake_worker():
        cmd = await bridge.next_command(wait=2.0)
        # A command submitted without on_progress silently ignores milestones.
        assert bridge.progress(cmd.id, "ignored") is False
        bridge.complete(cmd.id, "ok")

    task = asyncio.create_task(fake_worker())
    await bridge.submit(agent_id="a", kind="bash", cmd="echo hi", timeout=2)
    await task


async def test_bridge_progress_sink_error_is_swallowed():
    bridge = WorkerBridge()

    def boom(_: str) -> None:
        raise RuntimeError("bad narrator")

    async def fake_worker():
        cmd = await bridge.next_command(wait=2.0)
        # A raising sink can't break the command — progress just reports False.
        assert bridge.progress(cmd.id, "x") is False
        bridge.complete(cmd.id, "result")

    task = asyncio.create_task(fake_worker())
    out = await bridge.submit(
        agent_id="a", kind="claude", cmd="build", timeout=2, on_progress=boom
    )
    assert out == "result"  # the command still completed cleanly
    await task


async def test_payload_shape():
    bridge = WorkerBridge()

    async def fake_worker():
        cmd = await bridge.next_command(wait=2.0)
        payload = cmd.to_payload()
        assert set(payload) == {
            "command_id", "agent_id", "kind", "cmd", "timeout", "task", "thought",
            "stream_progress",
        }
        assert payload["stream_progress"] is False  # default off; builds opt in
        bridge.complete(cmd.id, "ok")

    task = asyncio.create_task(fake_worker())
    await bridge.submit(
        agent_id="a9", kind="claude", cmd="build it", timeout=2,
        task="the task", thought="my reasoning",
    )
    await task


# ── agent dispatch tests ──────────────────────────────────────────

async def test_agent_dispatches_bash_to_worker_when_online(monkeypatch):
    a = TerminalAgent("task")
    captured: dict = {}

    class FakeBridge:
        def worker_online(self):
            return True

        async def submit(self, **kw):
            captured.update(kw)
            return "worker-ran-it"

    monkeypatch.setattr(agent_mod, "get_worker_bridge", lambda: FakeBridge())
    out = await a._run_command("bash", "ls -la", 30)
    assert out == "worker-ran-it"
    assert captured["agent_id"] == a.id
    assert captured["kind"] == "bash"
    assert captured["cmd"] == "ls -la"


async def test_claude_requires_worker_when_offline(monkeypatch):
    a = TerminalAgent("task")

    class FakeBridge:
        def worker_online(self):
            return False

    monkeypatch.setattr(agent_mod, "get_worker_bridge", lambda: FakeBridge())
    out = await a._run_command("claude", "do a big refactor", 300)
    assert "scrappy worker" in out.lower()


async def test_bash_falls_back_to_local_exec_when_offline(monkeypatch):
    a = TerminalAgent("task")

    class FakeBridge:
        def worker_online(self):
            return False

    async def fake_exec(cmd, cwd, timeout):
        return f"local:{cmd}"

    monkeypatch.setattr(agent_mod, "get_worker_bridge", lambda: FakeBridge())
    monkeypatch.setattr(agent_mod, "_exec", fake_exec)
    out = await a._run_command("bash", "echo hi", 30)
    assert out == "local:echo hi"


# ── tool surface tests ────────────────────────────────────────────

def test_router_exposes_bash_and_claude_without_memory():
    a = TerminalAgent("task")  # no semantic store wired
    names = [t["function"]["name"] for t in _AgentToolRouter(a).tools_payload()]
    assert "bash" in names
    assert "claude" in names
    assert not any(n.startswith("memory.") for n in names)


def test_router_adds_memory_tools_when_wired():
    a = TerminalAgent(
        "task", semantic=MagicMock(), embedder=MagicMock(), user_uuid="u"
    )
    names = [t["function"]["name"] for t in _AgentToolRouter(a).tools_payload()]
    assert "memory.save" in names
    assert "memory.search" in names


# ── worker endpoint tests ─────────────────────────────────────────

@pytest.fixture
def client():
    from apps.api.main import app
    return TestClient(app)


def test_worker_heartbeat_then_status_online(client):
    r = client.post("/v1/worker/heartbeat")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/v1/worker/status")
    assert r.status_code == 200
    assert r.json()["online"] is True


def test_worker_result_unknown_command(client):
    r = client.post("/v1/worker/result", json={"command_id": "nope", "output": "x"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_worker_progress_unknown_command(client):
    # Endpoint routes to the bridge; an unknown/finished command yields ok False
    # (and the POST still marks the worker online, like a heartbeat).
    r = client.post("/v1/worker/progress", json={"command_id": "nope", "chunk": "hi"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert client.get("/v1/worker/status").json()["online"] is True


# ── coder connector (claude on the worker) ────────────────────────

async def test_coder_requires_worker_when_offline(monkeypatch):
    class FakeBridge:
        def worker_online(self):
            return False

    monkeypatch.setattr(coder_mod, "get_worker_bridge", lambda: FakeBridge())
    out = await CoderConnector().invoke(
        "code", {"prompt": "build a parser"}, InvocationContext(session_id="s")
    )
    assert "scrappy worker" in out.lower()


async def test_coder_dispatches_claude_when_online(monkeypatch):
    captured: dict = {}

    class FakeBridge:
        def worker_online(self):
            return True

        async def submit(self, **kw):
            captured.update(kw)
            return "claude-result"

    monkeypatch.setattr(coder_mod, "get_worker_bridge", lambda: FakeBridge())
    out = await CoderConnector().invoke(
        "code", {"prompt": "build a parser"}, InvocationContext(session_id="abcd1234ef")
    )
    assert out == "claude-result"
    assert captured["kind"] == "claude"
    assert captured["cmd"] == "build a parser"
