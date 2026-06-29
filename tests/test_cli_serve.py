"""IM-2.1 milestone 2 — `scrappy serve` runs the backend locally.

We don't start a real server; we inject a fake runner and assert the wiring:
it serves the FastAPI app on localhost by default and honours the host/port env
overrides.
"""

from __future__ import annotations

from apps import cli


def test_serve_api_defaults_to_localhost(monkeypatch):
    captured = {}

    def fake_run(app, host, port, log_level):
        captured.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.delenv("SCRAPPY_SERVE_HOST", raising=False)
    monkeypatch.delenv("SCRAPPY_SERVE_PORT", raising=False)
    cli._serve_api(run=fake_run)

    assert captured["app"] == "apps.api.main:app"
    assert captured["host"] == "127.0.0.1"  # not exposed on the LAN
    assert captured["port"] == 8000
    assert captured["log_level"] == captured["log_level"].lower()


def test_serve_api_honours_env_overrides(monkeypatch):
    captured = {}
    monkeypatch.setenv("SCRAPPY_SERVE_HOST", "0.0.0.0")
    monkeypatch.setenv("SCRAPPY_SERVE_PORT", "9100")
    cli._serve_api(run=lambda app, host, port, log_level: captured.update(host=host, port=port))

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9100
