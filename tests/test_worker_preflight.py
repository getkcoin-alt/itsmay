"""IM-1.2 worker preflight — terminal.spawn reports where the agent will run."""

from __future__ import annotations

from core.connectors.base import InvocationContext
from core.connectors.terminal import connector as term_conn


class _FakeAgent:
    id = "ab12cd34"
    status = "pending"
    work_dir = "/tmp/scrappy-agent-ab12cd34"


class _FakeStore:
    def spawn(self, task, llm, **kw):
        return _FakeAgent()


class _FakeBridge:
    def __init__(self, online: bool):
        self._online = online

    def worker_online(self) -> bool:
        return self._online


def _patch(monkeypatch, online: bool):
    monkeypatch.setattr(term_conn, "get_agent_store", lambda: _FakeStore())
    monkeypatch.setattr(term_conn, "get_worker_bridge", lambda: _FakeBridge(online))


async def test_spawn_warns_when_no_worker(monkeypatch):
    _patch(monkeypatch, online=False)
    res = await term_conn.TerminalConnector().invoke(
        "spawn", {"task": "open chrome"}, InvocationContext(llm=object())
    )
    assert res["worker_connected"] is False
    msg = res["message"].lower()
    assert "server" in msg
    assert "scrappy worker" in msg
    assert "cannot touch" in msg


async def test_spawn_notes_worker_connected(monkeypatch):
    _patch(monkeypatch, online=True)
    res = await term_conn.TerminalConnector().invoke(
        "spawn", {"task": "open chrome"}, InvocationContext(llm=object())
    )
    assert res["worker_connected"] is True
    assert "mac" in res["message"].lower()


async def test_spawn_still_requires_llm(monkeypatch):
    _patch(monkeypatch, online=True)
    res = await term_conn.TerminalConnector().invoke(
        "spawn", {"task": "x"}, InvocationContext(llm=None)
    )
    assert res.get("ok") is False
