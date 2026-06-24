"""Tests for TerminalAgent, AgentStore, and the /v1/agents REST endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.terminal_agent.agent import TerminalAgent, LogEntry
from core.terminal_agent.registry import AgentStore


# ── TerminalAgent unit tests ──────────────────────────────────────

def test_initial_state():
    a = TerminalAgent("echo hello")
    assert a.status == "pending"
    assert a.task == "echo hello"
    assert a.result is None
    assert a.log == []
    assert a.finished_at is None
    assert len(a.id) == 8


def test_add_log_appends_entries():
    a = TerminalAgent("task")
    a._add_log("cmd", "ls -la")
    a._add_log("output", "total 0")
    assert len(a.log) == 2
    assert a.log[0].kind == "cmd"
    assert a.log[0].text == "ls -la"
    assert a.log[1].kind == "output"
    assert isinstance(a.log[0].ts, str)


def test_to_dict_summary():
    a = TerminalAgent("run tests")
    a._add_log("cmd", "pytest")
    d = a.to_dict()
    assert d["task"] == "run tests"
    assert d["status"] == "pending"
    assert d["log_count"] == 1
    assert "log" not in d  # not full by default
    assert "work_dir" in d


def test_to_dict_full_includes_log():
    a = TerminalAgent("run tests")
    a._add_log("result", "all passed")
    d = a.to_dict(full=True)
    assert "log" in d
    assert len(d["log"]) == 1
    assert d["log"][0]["kind"] == "result"
    assert d["log"][0]["text"] == "all passed"


def test_cancel_without_task_returns_false():
    a = TerminalAgent("task")
    # No asyncio task launched — cancel should be a no-op.
    assert a.cancel() is False
    assert a.status == "pending"
    assert a.result is None


def test_cancel_running_task():
    a = TerminalAgent("task")
    mock_task = MagicMock()
    mock_task.done.return_value = False
    a._asyncio_task = mock_task
    a.status = "running"

    result = a.cancel()

    assert result is True
    mock_task.cancel.assert_called_once()
    assert a.status == "error"
    assert a.result == "Cancelled by user"
    assert a.finished_at is not None
    assert any(e.kind == "error" and "Cancelled" in e.text for e in a.log)


def test_cancel_already_done_task_returns_false():
    a = TerminalAgent("task")
    mock_task = MagicMock()
    mock_task.done.return_value = True  # already finished
    a._asyncio_task = mock_task
    a.status = "done"

    assert a.cancel() is False
    assert a.status == "done"  # unchanged


# ── AgentStore unit tests ─────────────────────────────────────────

def test_store_spawn_returns_agent():
    store = AgentStore()
    mock_llm = MagicMock()
    with patch.object(TerminalAgent, "launch"):
        agent = store.spawn("test task", mock_llm)
    assert agent.task == "test task"
    assert agent.status == "pending"


def test_store_get_by_id():
    store = AgentStore()
    with patch.object(TerminalAgent, "launch"):
        a = store.spawn("task", MagicMock())
    assert store.get(a.id) is a


def test_store_get_missing_returns_none():
    store = AgentStore()
    assert store.get("nope") is None


def test_store_list_newest_first():
    store = AgentStore()
    with patch.object(TerminalAgent, "launch"):
        a1 = store.spawn("first", MagicMock())
        a2 = store.spawn("second", MagicMock())
        a3 = store.spawn("third", MagicMock())
    agents = store.list_all()
    assert [a.task for a in agents] == ["third", "second", "first"]


def test_store_evicts_oldest_at_capacity():
    store = AgentStore()
    store._MAX = 3
    with patch.object(TerminalAgent, "launch"):
        ids = [store.spawn(f"task {i}", MagicMock()).id for i in range(4)]
    remaining = [a.id for a in store.list_all()]
    assert len(remaining) == 3
    assert ids[0] not in remaining  # oldest evicted


def test_store_launch_receives_llm():
    store = AgentStore()
    mock_llm = MagicMock()
    launched_with = []

    def fake_launch(self, llm):
        launched_with.append(llm)

    with patch.object(TerminalAgent, "launch", fake_launch):
        store.spawn("task", mock_llm)

    assert launched_with == [mock_llm]


# ── REST endpoint tests ───────────────────────────────────────────

@pytest.fixture
def client_with_store():
    """FastAPI TestClient with a seeded AgentStore injected."""
    store = AgentStore()
    mock_llm = MagicMock()
    with patch.object(TerminalAgent, "launch"):
        a1 = store.spawn("write a script", mock_llm)
        a2 = store.spawn("run benchmarks", mock_llm)

    with patch("apps.api.routers.agents.get_agent_store", return_value=store):
        from apps.api.main import app
        yield TestClient(app), store, [a1, a2]


def test_list_agents_returns_all(client_with_store):
    client, store, agents = client_with_store
    resp = client.get("/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    tasks = {d["task"] for d in data}
    assert tasks == {"write a script", "run benchmarks"}


def test_list_agents_no_log_field(client_with_store):
    client, store, agents = client_with_store
    resp = client.get("/v1/agents")
    for item in resp.json():
        assert "log" not in item
        assert "log_count" in item


def test_get_agent_returns_full(client_with_store):
    client, store, agents = client_with_store
    resp = client.get(f"/v1/agents/{agents[0].id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == agents[0].id
    assert "log" in data  # full=True for detail view


def test_get_agent_404(client_with_store):
    client, *_ = client_with_store
    resp = client.get("/v1/agents/doesnotexist")
    assert resp.status_code == 404


def test_cancel_agent_no_task(client_with_store):
    client, store, agents = client_with_store
    # Agent has no asyncio task — cancel() returns False.
    resp = client.delete(f"/v1/agents/{agents[0].id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is False
    assert data["status"] == "pending"


def test_cancel_agent_running(client_with_store):
    client, store, agents = client_with_store
    # Simulate a running task.
    mock_task = MagicMock()
    mock_task.done.return_value = False
    agents[0]._asyncio_task = mock_task
    agents[0].status = "running"

    resp = client.delete(f"/v1/agents/{agents[0].id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is True
    assert data["status"] == "error"


def test_cancel_agent_404(client_with_store):
    client, *_ = client_with_store
    resp = client.delete("/v1/agents/doesnotexist")
    assert resp.status_code == 404
