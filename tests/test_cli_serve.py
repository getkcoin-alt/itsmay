"""IM-2.1 milestone 2 — `scrappy serve` runs the backend locally.

We don't start a real server; we inject a fake runner and assert the wiring:
it serves the FastAPI app on localhost by default and honours the host/port env
overrides.
"""

from __future__ import annotations

import asyncio
import os

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


async def test_up_runs_both_and_aligns_api_base(monkeypatch):
    """`scrappy up` runs server + worker in one loop, points the worker at the
    server it launched, and cancels the worker when the server stops."""
    monkeypatch.setattr(cli, "API_BASE", cli.API_BASE)  # auto-restored at teardown
    monkeypatch.setenv("SCRAPPY_SERVE_PORT", "8123")
    monkeypatch.delenv("SCRAPPY_SERVE_HOST", raising=False)

    started = {"server": False, "worker": False, "worker_cancelled": False}

    async def fake_server():
        started["server"] = True
        await asyncio.sleep(0.05)  # finish so _up returns

    async def fake_worker():
        started["worker"] = True
        try:
            await asyncio.sleep(30)  # would run forever — must be cancelled
        except asyncio.CancelledError:
            started["worker_cancelled"] = True
            raise

    await cli._up(run_server=fake_server, run_worker=fake_worker)

    assert started["server"] is True
    assert started["worker"] is True
    assert started["worker_cancelled"] is True  # cancelled on server stop
    assert cli.API_BASE == "http://127.0.0.1:8123"
    assert os.environ["VAULT_API_BASE"] == "http://127.0.0.1:8123"
