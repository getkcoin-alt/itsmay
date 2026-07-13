"""IM-5.4 — self-modification guardrails.

The safety floor for Epic 5: which files self-modification may touch, and the
enable/freeze kill switch. Pure policy — no network, no real home dir (the freeze
marker is redirected to tmp).
"""

from __future__ import annotations

import types

import pytest

from core.identity import self_guard
from core.identity.self_guard import check_change


def _settings(self_modify: bool):
    return types.SimpleNamespace(self_modify=self_modify)

# ── protected: the invariants that keep self-editing safe ─────────────


@pytest.mark.parametrize(
    "path",
    [
        "apps/api/middleware/auth.py",   # #12 auth gate
        "core/brain/agent_loop.py",      # #13 approval enforcement
        "core/brain/orchestrator.py",    # #13
        "core/connectors/registry.py",   # #13 choke point
        "core/util/keypool.py",          # #14
        "core/config.py",                # secrets defaults
        "core/identity/self_guard.py",   # the guard can't edit its own guard
        "pyproject.toml",
        "Dockerfile",
    ],
)
def test_security_critical_files_are_protected(path):
    v = check_change([path], enabled=True)
    assert v.allowed is False
    assert [p for p, _ in v.rejected] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",                    # absolute
        "../secrets.txt",                 # traversal
        "core/../../escape.py",           # traversal (nested)
        ".git/config",                    # git internals
        ".github/workflows/ci.yml",       # CI
        ".env",                           # secret
        "deploy/config.env",              # secret
        "certs/server.pem",               # credential
        "keys/id_rsa.key",                # credential
    ],
)
def test_out_of_scope_paths_rejected(path):
    assert check_change([path], enabled=True).allowed is False


@pytest.mark.parametrize(
    "path",
    [
        "apps/api/routers/chat.py",
        "core/connectors/web/connector.py",
        "core/memory/procedural.py",
        "tests/test_new_thing.py",
        "docs/memory.md",
        "apps/api/routers/brand_new.py",  # a brand-new file is fine
    ],
)
def test_ordinary_source_files_allowed(path):
    v = check_change([path], enabled=True)
    assert v.allowed is True
    assert v.rejected == []


def test_one_protected_path_rejects_the_whole_change():
    v = check_change(["apps/api/routers/chat.py", "core/util/keypool.py"], enabled=True)
    assert v.allowed is False
    assert [p for p, _ in v.rejected] == ["core/util/keypool.py"]


def test_disabled_rejects_everything():
    v = check_change(["apps/api/routers/chat.py"], enabled=False)
    assert v.allowed is False
    assert "disabled or frozen" in v.reason


def test_empty_change_rejected():
    assert check_change([], enabled=True).allowed is False


def test_verdict_serializes():
    d = check_change(["core/util/keypool.py"], enabled=True).to_dict()
    assert d["allowed"] is False
    assert d["rejected"][0]["path"] == "core/util/keypool.py"
    assert d["rejected"][0]["why"]


# ── kill switch: freeze / config ──────────────────────────────────────


@pytest.fixture
def tmp_marker(tmp_path, monkeypatch):
    marker = tmp_path / "self_modify.frozen"
    monkeypatch.setattr(self_guard, "_freeze_marker", lambda: marker)
    return marker


def test_freeze_unfreeze_roundtrip(tmp_marker):
    assert self_guard.is_frozen() is False
    self_guard.freeze()
    assert self_guard.is_frozen() is True
    assert self_guard.unfreeze() is True
    assert self_guard.is_frozen() is False
    assert self_guard.unfreeze() is False  # idempotent


def test_self_modify_enabled_respects_config_and_freeze(tmp_marker, monkeypatch):
    monkeypatch.setattr(self_guard, "get_settings", lambda: _settings(True))
    assert self_guard.self_modify_enabled() is True
    self_guard.freeze()
    assert self_guard.self_modify_enabled() is False  # frozen at runtime
    self_guard.unfreeze()
    monkeypatch.setattr(self_guard, "get_settings", lambda: _settings(False))
    assert self_guard.self_modify_enabled() is False  # SELF_MODIFY=off


def test_check_change_reads_live_switch_when_enabled_unset(tmp_marker, monkeypatch):
    monkeypatch.setattr(self_guard, "get_settings", lambda: _settings(True))
    assert check_change(["apps/api/routers/chat.py"]).allowed is True
    self_guard.freeze()
    assert check_change(["apps/api/routers/chat.py"]).allowed is False
