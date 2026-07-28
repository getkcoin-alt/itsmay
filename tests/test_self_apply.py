"""IM-5.2 — self.apply_change (approval-gated) + self.rollback.

Offline: the worker bridge is faked (responds to the diff / apply / rollback
commands); the guard, script builders, and audit memory are real. Nothing here
touches real git — the bridge stands in for the Mac worker.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np

from core.connectors.registry import get_registry
from core.identity import apply as apply_mod
from core.identity.apply import apply_change, build_apply_script, build_rollback_script, rollback

_USER = uuid4()
_BRANCH = "scrappy/self-add-uptime-endpoint"


class FakeEmbedder:
    async def embed(self, text: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


class RecordingSemantic:
    def __init__(self) -> None:
        self.written: list[dict] = []

    async def write(self, user_id, kind, content, embedding, *, source=None, importance=0.5):
        self.written.append({"kind": kind, "content": content, "source": source})
        return uuid4()


class FakeBridge:
    def __init__(self, responder, online: bool = True) -> None:
        self._responder = responder
        self._online = online
        self.calls: list[dict] = []

    def worker_online(self) -> bool:
        return self._online

    async def submit(self, *, agent_id, kind, cmd, timeout, task=""):  # noqa: ASYNC109
        self.calls.append({"kind": kind, "cmd": cmd})
        return self._responder(cmd)


def _responder(
    diff="apps/api/routers/x.py\ntests/test_x.py",
    apply="APPLY_OK abc",
    rb="ROLLED_BACK abc",
):
    def r(cmd: str) -> str:
        if "diff --name-only" in cmd:
            return diff
        if "merge --no-ff" in cmd:  # the apply script
            return apply
        return rb  # the rollback script
    return r


# ── script builders (pure) ────────────────────────────────────────────


def test_apply_script_is_safe_and_atomic():
    s = build_apply_script(_BRANCH, "add uptime")
    assert "git tag -f last-good HEAD" in s
    assert f"merge --no-ff {_BRANCH}" in s
    assert "pytest" in s
    assert "git reset --hard last-good" in s  # auto-rollback on test failure
    assert "Scrappy Singh" in s  # authored as Scrappy
    assert "APPLY_OK" in s


def test_rollback_script_targets_last_good():
    s = build_rollback_script()
    assert "reset --hard last-good" in s
    assert "ROLLED_BACK" in s


# ── apply_change orchestration ────────────────────────────────────────


async def test_apply_happy_path_merges_and_audits(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    sem = RecordingSemantic()
    bridge = FakeBridge(_responder())
    res = await apply_change(
        _BRANCH, notes="add an uptime endpoint", bridge=bridge,
        semantic=sem, embedder=FakeEmbedder(), user_id=_USER,
    )

    assert res.applied is True
    assert res.restart_needed is True
    assert res.guard["allowed"] is True
    assert res.outcome.startswith("APPLY_OK")
    # Two worker round-trips: preflight diff, then the apply script.
    assert [c["kind"] for c in bridge.calls] == ["bash", "bash"]
    assert "diff --name-only" in bridge.calls[0]["cmd"]
    assert "merge --no-ff" in bridge.calls[1]["cmd"]
    # Audit memory recorded.
    assert len(sem.written) == 1
    assert sem.written[0]["source"] == "self-apply"
    assert sem.written[0]["kind"] == "reflection"
    assert "changed my own code" in sem.written[0]["content"]


async def test_apply_refuses_protected_diff_before_merging(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    sem = RecordingSemantic()
    bridge = FakeBridge(_responder(diff="core/util/keypool.py"))
    res = await apply_change(
        _BRANCH, bridge=bridge, semantic=sem, embedder=FakeEmbedder(), user_id=_USER
    )

    assert res.applied is False
    assert res.guard["allowed"] is False
    assert "Refused" in res.summary
    # Guard runs BEFORE the merge: only the diff round-trip happened.
    assert len(bridge.calls) == 1
    assert sem.written == []  # no audit for a refused change


async def test_apply_auto_rolls_back_on_test_failure(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(_responder(apply="APPLY_ROLLED_BACK tests-failed"))
    res = await apply_change(_BRANCH, bridge=bridge)

    assert res.applied is False
    assert res.restart_needed is False
    assert "rolled back" in res.summary.lower()


async def test_apply_reports_merge_conflict(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(_responder(apply="APPLY_FAILED merge-conflict"))
    res = await apply_change(_BRANCH, bridge=bridge)

    assert res.applied is False
    assert "did not complete" in res.summary


async def test_apply_rejects_non_self_branch(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(_responder())
    res = await apply_change("feature/not-mine", bridge=bridge)

    assert res.applied is False
    assert bridge.calls == []  # never touched the worker


async def test_apply_refuses_when_frozen(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: False)
    bridge = FakeBridge(_responder())
    res = await apply_change(_BRANCH, bridge=bridge)

    assert res.applied is False
    assert "frozen" in res.summary.lower()
    assert bridge.calls == []


async def test_apply_refuses_without_worker(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(_responder(), online=False)
    res = await apply_change(_BRANCH, bridge=bridge)

    assert res.applied is False
    assert bridge.calls == []


async def test_apply_refuses_empty_diff(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(_responder(diff=""))
    res = await apply_change(_BRANCH, bridge=bridge)

    assert res.applied is False
    assert res.guard["allowed"] is False
    assert len(bridge.calls) == 1  # diff only; never merged


# ── rollback ──────────────────────────────────────────────────────────


async def test_rollback_success(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    res = await rollback(bridge=FakeBridge(_responder()))
    assert res.ok is True
    assert "Rolled main back" in res.summary


async def test_rollback_no_tag(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: True)
    res = await rollback(bridge=FakeBridge(_responder(rb="ROLLBACK_FAILED no-last-good-tag")))
    assert res.ok is False


async def test_rollback_refused_when_frozen(monkeypatch):
    monkeypatch.setattr(apply_mod, "self_modify_enabled", lambda: False)
    bridge = FakeBridge(_responder())
    res = await rollback(bridge=bridge)
    assert res.ok is False
    assert bridge.calls == []


# ── connector wiring / approval gating ────────────────────────────────


def test_apply_requires_approval_rollback_does_not():
    reg = get_registry()
    apply_tool = reg.get_tool("self.apply_change")
    rb_tool = reg.get_tool("self.rollback")
    assert apply_tool is not None and rb_tool is not None
    assert apply_tool.spec.requires_approval is True  # the operator gates the merge
    assert rb_tool.spec.requires_approval is False  # recovery must be immediate
    assert apply_tool.spec.executor == "server"
