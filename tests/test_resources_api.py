"""The resource ledger over HTTP.

Follows `test_vault_api.py`: the real `create_app()` wiring and middleware, with
a temp-file ledger attached to `app.state` (the lifespan does not run under a
bare `TestClient`, so the state the router reads is set here).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import create_app  # noqa: E402
from core.resources import (  # noqa: E402
    Resource,
    ResourceLedger,
    SqliteResourceStore,
)


@pytest.fixture
def client(tmp_path):
    store = SqliteResourceStore(str(tmp_path / "res.db"))
    app = create_app()
    app.state.ledger = ResourceLedger(store, account="primary")
    try:
        yield TestClient(app)
    finally:
        store.close()


def test_usage_starts_empty_but_well_formed(client):
    r = client.get("/v1/resources")
    assert r.status_code == 200
    body = r.json()
    assert body["account"] == "primary"
    assert body["usage"] == []


def test_setting_a_ceiling_then_reading_it_back(client):
    r = client.put("/v1/resources/usd/ceiling", json={"limit": 25.0})
    assert r.status_code == 200
    assert r.json()["ceiling"] == 25.0

    r = client.get("/v1/resources/usd")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == "usd"
    assert body["ceiling"] == 25.0
    assert body["used"] == 0.0
    assert body["remaining"] == 25.0


def test_a_null_limit_removes_the_ceiling(client):
    client.put("/v1/resources/calls/ceiling", json={"limit": 1})
    r = client.put("/v1/resources/calls/ceiling", json={"limit": None})
    assert r.status_code == 200
    body = r.json()
    # Uncapped reports no ceiling and no remaining — not zero remaining.
    assert body["ceiling"] is None
    assert body["remaining"] is None


def test_a_negative_ceiling_is_rejected_with_422(client):
    r = client.put("/v1/resources/usd/ceiling", json={"limit": -5})
    assert r.status_code == 422


def test_an_unknown_resource_is_rejected_rather_than_invented(client):
    r = client.get("/v1/resources/bitcoin")
    assert r.status_code == 422


def test_accounts_are_addressable_separately(client):
    client.put("/v1/resources/calls/ceiling", json={"limit": 3}, params={"account": "cust_a"})
    a = client.get("/v1/resources/calls", params={"account": "cust_a"}).json()
    b = client.get("/v1/resources/calls", params={"account": "cust_b"}).json()
    assert a["ceiling"] == 3
    assert b["ceiling"] is None
    assert b["account"] == "cust_b"


def test_the_endpoint_reports_what_the_ledger_metered(client):
    import anyio

    ledger: ResourceLedger = client.app.state.ledger

    async def meter() -> None:
        await ledger.set_ceiling(Resource.SEARCHES, 10)
        await ledger.charge(Resource.SEARCHES, 4, reason="test")
        await ledger.charge(Resource.MEMORIES, 2, reason="test")

    anyio.run(meter)

    rows = {u["resource"]: u for u in client.get("/v1/resources").json()["usage"]}
    assert rows["searches"]["used"] == 4
    assert rows["searches"]["remaining"] == 6
    assert rows["memories"]["used"] == 2
    assert rows["memories"]["period"] == "total"


def test_the_router_reports_503_when_no_ledger_is_wired():
    app = create_app()
    app.state.ledger = None
    r = TestClient(app).get("/v1/resources")
    assert r.status_code == 503
