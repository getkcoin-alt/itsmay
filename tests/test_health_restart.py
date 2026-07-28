"""IM-5.2 (rest) — health probe + restart signal (the verify/restart primitives)."""

from __future__ import annotations

import types

from core.identity import restart
from core.identity.health import probe, wait_healthy


class FakeClient:
    """Returns the given statuses in order (last repeats). 'raise' → connection error."""

    def __init__(self, statuses):
        self._q = list(statuses)
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        s = self._q.pop(0) if len(self._q) > 1 else self._q[0]
        if s == "raise":
            raise RuntimeError("connection refused")
        return types.SimpleNamespace(status_code=s)

    async def aclose(self):
        pass


# ── probe ─────────────────────────────────────────────────────────────


async def test_probe_healthy_on_200():
    r = await probe("http://x", client=FakeClient([200]))
    assert r == {"healthy": True, "status": 200}


async def test_probe_unhealthy_on_non_200():
    r = await probe("http://x", client=FakeClient([503]))
    assert r["healthy"] is False and r["status"] == 503


async def test_probe_handles_connection_error():
    r = await probe("http://x", client=FakeClient(["raise"]))
    assert r["healthy"] is False and r["status"] is None
    assert "error" in r


# ── wait_healthy ──────────────────────────────────────────────────────


async def test_wait_healthy_succeeds_after_retries():
    client = FakeClient([503, 503, 200])
    r = await wait_healthy("http://x", attempts=5, delay=0, client=client)
    assert r["healthy"] is True
    assert r["attempts"] == 3


async def test_wait_healthy_gives_up():
    r = await wait_healthy("http://x", attempts=3, delay=0, client=FakeClient(["raise"]))
    assert r["healthy"] is False
    assert r["attempts"] == 3


# ── restart signal ────────────────────────────────────────────────────


def test_restart_marker_lifecycle(tmp_path, monkeypatch):
    marker = tmp_path / "restart_requested"
    monkeypatch.setattr(restart, "_marker", lambda: marker)

    assert restart.pending_restart() is None
    restart.request_restart(reason="applied scrappy/self-x")
    assert restart.pending_restart() == "applied scrappy/self-x"
    restart.clear_restart()
    assert restart.pending_restart() is None
    restart.clear_restart()  # idempotent


def test_request_restart_empty_reason_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, "_marker", lambda: tmp_path / "m")
    restart.request_restart()
    assert restart.pending_restart() == "restart"
