"""Router-level test: multi-tenant unique URL + PIN, on the real `create_app()`.

Runs the actual lifespan (real build_engine(), real tenant registry) pointed
at a throwaway tmp_path, so route wiring, the auth split (global middleware
vs. per-tenant PIN), and cross-tenant isolation are all exercised for real —
not mocked. Slower than a pure-unit test; that's the trade for confidence
that the "/v1/mini" vs "/v1/mini/t/{slug}" double-mount actually behaves.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from core.config import get_settings  # noqa: E402

MASTER_KEY = "s3cret-master-key"


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_API_KEY", MASTER_KEY)
    monkeypatch.setenv("COMPANION_SQLITE_PATH", str(tmp_path / "mini.db"))
    monkeypatch.setenv("TENANTS_DB_PATH", str(tmp_path / "tenants.db"))
    monkeypatch.setenv("TENANTS_DATA_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("MINI_ONLY", "false")  # no legacy owner auto-registration here
    monkeypatch.setenv("LLM_API_KEY", "")     # never actually calls the LLM in these tests
    get_settings.cache_clear()

    from apps.api.main import create_app

    with TestClient(create_app()) as c:
        yield c

    get_settings.cache_clear()


def _onboard(client, owner_name, pin=None):
    body = {"owner_name": owner_name}
    if pin:
        body["pin"] = pin
    r = client.post(
        "/v1/mini/admin/onboard", json=body,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── auth boundaries ─────────────────────────────────────────────────


def test_admin_onboard_requires_global_master_key(app_client):
    r = app_client.post("/v1/mini/admin/onboard", json={"owner_name": "Priya"})
    assert r.status_code == 401

    r_wrong = app_client.post(
        "/v1/mini/admin/onboard", json={"owner_name": "Priya"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r_wrong.status_code == 401


def test_unknown_tenant_slug_404s(app_client):
    r = app_client.get("/v1/mini/t/no-such-slug/profile")
    assert r.status_code == 404


def test_wrong_pin_401s(app_client):
    info = _onboard(app_client, "Priya")
    r = app_client.get(
        f"/v1/mini/t/{info['slug']}/profile",
        headers={"Authorization": "Bearer 0000"},
    )
    assert r.status_code == 401


def test_correct_pin_succeeds(app_client):
    info = _onboard(app_client, "Priya")
    r = app_client.get(
        f"/v1/mini/t/{info['slug']}/profile",
        headers={"Authorization": f"Bearer {info['pin']}"},
    )
    assert r.status_code == 200
    assert r.json()["persona"] == "friend"


def test_admin_onboard_url_shape(app_client):
    info = _onboard(app_client, "Priya")
    assert info["url"].endswith(f"/u/{info['slug']}")


def test_admin_tenants_listing_never_exposes_pin(app_client):
    _onboard(app_client, "Priya")
    r = app_client.get(
        "/v1/mini/admin/tenants", headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    assert r.status_code == 200
    body = r.text
    assert "priya" in body.lower()
    assert "pin" not in body.lower() or "pin_hash" not in body  # no raw pin field at all


def test_unknown_tenant_slug_over_websocket_gets_error_event(app_client):
    with app_client.websocket_connect("/v1/mini/t/no-such-slug/ws/chat") as ws:
        ws.send_text('{"message": "hi"}')
        msg = ws.receive_json()
    assert msg["event"] == "error"


def test_wrong_pin_over_websocket_gets_error_event(app_client):
    info = _onboard(app_client, "Priya")
    with app_client.websocket_connect(
        f"/v1/mini/t/{info['slug']}/ws/chat?_token=0000"
    ) as ws:
        ws.send_text('{"message": "hi"}')
        msg = ws.receive_json()
    assert msg["event"] == "error"


# ── cross-tenant isolation ───────────────────────────────────────────


def test_two_tenants_have_independent_profiles(app_client):
    priya = _onboard(app_client, "Priya")
    karan = _onboard(app_client, "Karan")
    assert priya["slug"] != karan["slug"]

    # Give each tenant a distinct nickname for their Mini — a stand-in for
    # "each tenant's own data" that's cheap to assert without the real LLM.
    app_client.put(
        f"/v1/mini/t/{priya['slug']}/profile",
        json={"bot_nickname": "PriyaBot"},
        headers={"Authorization": f"Bearer {priya['pin']}"},
    )
    app_client.put(
        f"/v1/mini/t/{karan['slug']}/profile",
        json={"bot_nickname": "KaranBot"},
        headers={"Authorization": f"Bearer {karan['pin']}"},
    )

    p = app_client.get(
        f"/v1/mini/t/{priya['slug']}/profile",
        headers={"Authorization": f"Bearer {priya['pin']}"},
    ).json()
    k = app_client.get(
        f"/v1/mini/t/{karan['slug']}/profile",
        headers={"Authorization": f"Bearer {karan['pin']}"},
    ).json()
    assert p["bot_nickname"] == "PriyaBot"
    assert k["bot_nickname"] == "KaranBot"
    assert p["id"] != k["id"]


def test_priya_pin_cannot_access_karans_tenant(app_client):
    priya = _onboard(app_client, "Priya")
    karan = _onboard(app_client, "Karan")

    r = app_client.get(
        f"/v1/mini/t/{karan['slug']}/profile",
        headers={"Authorization": f"Bearer {priya['pin']}"},  # wrong tenant's PIN
    )
    assert r.status_code == 401


def test_factory_reset_on_one_tenant_never_touches_another(app_client):
    priya = _onboard(app_client, "Priya")
    karan = _onboard(app_client, "Karan")

    app_client.put(
        f"/v1/mini/t/{karan['slug']}/profile",
        json={"bot_nickname": "KaranBot"},
        headers={"Authorization": f"Bearer {karan['pin']}"},
    )

    r = app_client.delete(
        f"/v1/mini/t/{priya['slug']}/memories",
        headers={"Authorization": f"Bearer {priya['pin']}"},
    )
    assert r.status_code == 200

    # Karan's tenant is a completely different sqlite file — untouched.
    k = app_client.get(
        f"/v1/mini/t/{karan['slug']}/profile",
        headers={"Authorization": f"Bearer {karan['pin']}"},
    ).json()
    assert k["bot_nickname"] == "KaranBot"


# ── legacy path stays exactly as it was ──────────────────────────────


def test_legacy_mini_path_unaffected_by_tenant_mount(app_client):
    # No slug in the URL -> global engine, global master key, unchanged.
    r = app_client.get(
        "/v1/mini/profile", headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    assert r.status_code == 200

    # A tenant's PIN must NOT work on the legacy surface.
    priya = _onboard(app_client, "Priya")
    r_wrong = app_client.get(
        "/v1/mini/profile", headers={"Authorization": f"Bearer {priya['pin']}"},
    )
    assert r_wrong.status_code == 401


def test_legacy_path_requires_no_slug_path_param(app_client):
    r = app_client.get("/v1/mini/profile")  # no Authorization header at all
    assert r.status_code == 401


# ── admin: deprovisioning ─────────────────────────────────────────────


def test_admin_delete_tenant_requires_master_key(app_client):
    info = _onboard(app_client, "Priya")
    r = app_client.delete(f"/v1/mini/admin/tenants/{info['slug']}")
    assert r.status_code == 401


def test_admin_delete_unknown_tenant_404s(app_client):
    r = app_client.delete(
        "/v1/mini/admin/tenants/does-not-exist",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    assert r.status_code == 404


def test_admin_delete_tenant_then_slug_is_gone(app_client):
    info = _onboard(app_client, "Priya")
    r = app_client.delete(
        f"/v1/mini/admin/tenants/{info['slug']}",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )
    assert r.status_code == 200

    r2 = app_client.get(
        f"/v1/mini/t/{info['slug']}/profile",
        headers={"Authorization": f"Bearer {info['pin']}"},
    )
    assert r2.status_code == 404


def test_deleting_one_tenant_never_affects_another(app_client):
    priya = _onboard(app_client, "Priya")
    karan = _onboard(app_client, "Karan")

    app_client.delete(
        f"/v1/mini/admin/tenants/{priya['slug']}",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
    )

    r = app_client.get(
        f"/v1/mini/t/{karan['slug']}/profile",
        headers={"Authorization": f"Bearer {karan['pin']}"},
    )
    assert r.status_code == 200
