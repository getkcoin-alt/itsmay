"""IM-5.3 — self.request_secret. The value never touches the model.

Units + the connector tool + the value-entry endpoint (config path redirected to
tmp so nothing real is written).
"""

from __future__ import annotations

import types

import pytest

from core.connectors.base import InvocationContext
from core.connectors.self_mod.connector import SelfConnector
from core.identity import secrets


def _stub_settings(monkeypatch, **fields):
    ns = types.SimpleNamespace(**fields)
    monkeypatch.setattr(secrets, "get_settings", lambda: ns)


# ── whitelist ─────────────────────────────────────────────────────────


def test_is_allowed_whitelist():
    assert secrets.is_allowed("elevenlabs_api_key")
    assert secrets.is_allowed("LLM_API_KEY")  # case-insensitive
    for blocked in ("vault_api_key", "database_url", "self_modify", "redis_url"):
        assert not secrets.is_allowed(blocked)
    assert not secrets.is_allowed("whatever")


# ── set_secret (file ops, hot-reload, guard) ──────────────────────────


def test_set_secret_writes_and_locks_down(tmp_path, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(secrets.get_settings, "cache_clear", lambda: calls.append(1))
    cfg = tmp_path / "config.env"

    key = secrets.set_secret("elevenlabs_api_key", "sk-abc123", config_path=cfg)
    assert key == "ELEVENLABS_API_KEY"
    assert "ELEVENLABS_API_KEY=sk-abc123" in cfg.read_text()
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"  # owner-only
    assert calls == [1]  # settings hot-reloaded


def test_set_secret_updates_existing_key(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets.get_settings, "cache_clear", lambda: None)
    cfg = tmp_path / "config.env"
    cfg.write_text("LLM_API_KEY=old\nOTHER=keep\n")

    secrets.set_secret("llm_api_key", "new", config_path=cfg)
    text = cfg.read_text()
    assert "LLM_API_KEY=new" in text
    assert "LLM_API_KEY=old" not in text
    assert "OTHER=keep" in text  # unrelated lines preserved


def test_set_secret_rejects_disallowed_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets.get_settings, "cache_clear", lambda: None)
    with pytest.raises(ValueError):
        secrets.set_secret("vault_api_key", "x", config_path=tmp_path / "c.env")
    with pytest.raises(ValueError):
        secrets.set_secret("elevenlabs_api_key", "", config_path=tmp_path / "c.env")


def test_secret_status_and_overview(monkeypatch):
    _stub_settings(monkeypatch, elevenlabs_api_key="set", llm_api_key="")
    assert secrets.secret_status("elevenlabs_api_key") is True
    assert secrets.secret_status("llm_api_key") is False
    by_name = {o["name"]: o for o in secrets.secrets_overview()}
    assert by_name["ELEVENLABS_API_KEY"]["set"] is True
    assert by_name["LLM_API_KEY"]["set"] is False


# ── connector tool ────────────────────────────────────────────────────


async def test_request_secret_tool_allowed(monkeypatch):
    _stub_settings(monkeypatch, elevenlabs_api_key="")
    out = await SelfConnector().invoke(
        "request_secret",
        {"name": "elevenlabs_api_key", "why": "expressive voice"},
        InvocationContext(),
    )
    assert out["secret_request"] == "ELEVENLABS_API_KEY"
    assert out["already_set"] is False
    assert "never passes through" in out["instruction"]


async def test_request_secret_tool_rejects_disallowed():
    out = await SelfConnector().invoke(
        "request_secret", {"name": "vault_api_key", "why": "nope"}, InvocationContext()
    )
    assert out["ok"] is False
    assert "elevenlabs_api_key" in out["requestable"]


def test_request_secret_tool_registered_not_gated():
    from core.connectors.registry import get_registry

    tool = get_registry().get_tool("self.request_secret")
    assert tool is not None
    assert tool.spec.requires_approval is False  # asking is safe


# ── endpoint (value in, never out) ────────────────────────────────────

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "_config_path", lambda: tmp_path / "config.env")
    monkeypatch.setattr(secrets.get_settings, "cache_clear", lambda: None)
    return TestClient(create_app())


def test_secret_endpoint_sets_without_echoing_value(client, tmp_path):
    r = client.post(
        "/v1/identity/secret", json={"name": "elevenlabs_api_key", "value": "sk-topsecret"}
    )
    assert r.status_code == 200
    assert r.json() == {"name": "ELEVENLABS_API_KEY", "set": True}  # value NOT echoed
    assert "sk-topsecret" not in r.text
    assert "ELEVENLABS_API_KEY=sk-topsecret" in (tmp_path / "config.env").read_text()


def test_secret_endpoint_rejects_disallowed(client):
    r = client.post("/v1/identity/secret", json={"name": "vault_api_key", "value": "x"})
    assert r.status_code == 400


def test_secrets_list_endpoint(client):
    r = client.get("/v1/identity/secrets")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["secrets"]}
    assert "ELEVENLABS_API_KEY" in names
