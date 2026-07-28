"""#19 — split the platform healthcheck off the live-upstream /v1/health.

/v1/live is process-liveness only (no LLM / embedder / DB), so a rate-limited or
cooling-down key can't fail the healthcheck and trigger a Railway restart loop.
/v1/health stays the deep readiness check.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import create_app  # noqa: E402


def _client() -> TestClient:
    # raise_server_exceptions=False so a failing deep-health dep surfaces as a 500
    # response instead of re-raising into the test.
    return TestClient(create_app(), raise_server_exceptions=False)


def test_live_is_static_ok():
    r = _client().get("/v1/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_live_decoupled_from_upstream_but_deep_health_is_not():
    # No lifespan ran → app.state.llm / embedder are unset. Liveness must not care;
    # the deep /v1/health depends on them and fails — exactly the split we want, so
    # the platform never restarts a healthy process over a cooled-down key.
    c = _client()
    assert c.get("/v1/live").status_code == 200
    assert c.get("/v1/health").status_code >= 500


def test_railway_healthcheck_targets_liveness():
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "railway.toml").read_text())
    assert cfg["deploy"]["healthcheckPath"] == "/v1/live"
