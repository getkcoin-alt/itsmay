def test_disable_instrument_blocks_trading(admin_client, user_client):
    assert admin_client.patch("/api/admin/assets/TITAN", json={"enabled": False}).status_code == 200
    o = user_client.post("/api/orders", json={"symbol": "TITAN", "side": "BUY", "quantity": 1}).json()["order"]
    assert o["status"] == "REJECTED" and "disabled" in o["reject_reason"]
    admin_client.patch("/api/admin/assets/TITAN", json={"enabled": True})
    o = user_client.post("/api/orders", json={"symbol": "TITAN", "side": "BUY", "quantity": 1}).json()["order"]
    assert o["status"] == "FILLED"


def test_price_override_applies_to_quotes_and_fills(admin_client, user_client):
    assert admin_client.patch("/api/admin/assets/NTPC", json={"target_price": 400}).status_code == 200
    q = user_client.get("/api/market/quote/NTPC").json()["quote"]
    assert abs(q["ltp"] - 400) < 2
    o = user_client.post("/api/orders", json={"symbol": "NTPC", "side": "BUY", "quantity": 1}).json()["order"]
    assert abs(o["filled_price"] - q["ltp"]) < 2
    assert admin_client.patch("/api/admin/assets/NTPC", json={"target_price": 99999}).status_code == 422
    admin_client.patch("/api/admin/assets/NTPC", json={"clear_override": True})


def test_fee_configuration(admin_client, user_client):
    r = admin_client.put("/api/admin/settings", json={"fees": {"crypto": {"pct": 0.002}}})
    assert r.status_code == 200
    o = user_client.post("/api/orders", json={"symbol": "XRP", "side": "BUY", "quantity": 100}).json()["order"]
    assert abs(o["fees"] - round(100 * o["filled_price"] * 0.002, 2)) < 0.02
    admin_client.put("/api/admin/settings", json={"fees": {"crypto": {"pct": 0.001}}})
    assert admin_client.put("/api/admin/settings", json={"fees": {"crypto": {"pct": 0.5}}}).status_code == 422
    assert admin_client.put("/api/admin/settings", json={"trading": {"bogus": 1}}).status_code == 422


def test_trading_halt_and_jurisdiction(admin_client, user_client):
    admin_client.put("/api/admin/settings", json={"trading": {"forex_inr_pairs_only": True}})
    o = user_client.post("/api/orders", json={"symbol": "EURUSD", "side": "BUY", "lots": 1}).json()["order"]
    assert o["status"] == "REJECTED" and "INR" in o["reject_reason"]
    assert user_client.post("/api/orders", json={"symbol": "USDINR", "side": "BUY", "lots": 1}).json()["order"]["status"] == "FILLED"
    admin_client.put("/api/admin/settings", json={"trading": {"forex_inr_pairs_only": False, "trading_halted": True}})
    o = user_client.post("/api/orders", json={"symbol": "ITC", "side": "BUY", "quantity": 1}).json()["order"]
    assert o["status"] == "REJECTED" and "halted" in o["reject_reason"]
    admin_client.put("/api/admin/settings", json={"trading": {"trading_halted": False}})


def test_kyc_review(admin_client, user_client):
    r = user_client.post("/api/profile/kyc", json={
        "legal_name": "Test Person", "pan": "ABCDE1234F", "city": "Pune", "occupation": "Engineer",
        "income_band": "₹5L–10L", "derivatives_experience": "None", "acknowledge_simulated": True,
    })
    assert r.status_code == 200 and r.json()["kyc"]["pan_masked"] == "XXXXX1234X"
    uid = user_client.get("/api/auth/me").json()["user"]["id"]
    pending = admin_client.get("/api/admin/kyc", params={"status": "PENDING"}).json()["records"]
    assert uid in [p["user_id"] for p in pending]
    assert admin_client.post(f"/api/admin/kyc/{uid}", json={"decision": "APPROVED"}).status_code == 200
    assert user_client.get("/api/profile").json()["user"]["kyc"]["status"] == "APPROVED"
    assert admin_client.post(f"/api/admin/kyc/{uid}", json={"decision": "REJECTED"}).status_code == 409


def test_audit_log_records_admin_actions(admin_client):
    admin_client.patch("/api/admin/assets/LT", json={"enabled": True})
    logs = admin_client.get("/api/admin/audit-logs", params={"action": "admin."}).json()["logs"]
    assert any(log["action"] == "admin.asset_updated" and log["target_id"] == "LT" for log in logs)


def test_reports_and_listings(admin_client):
    r = admin_client.get("/api/admin/reports", params={"days": 30}).json()
    assert len(r["daily"]) == 30 and r["top_instruments"]
    assert admin_client.get("/api/admin/orders").json()["orders"]
    assert admin_client.get("/api/admin/transactions").json()["transactions"]
    users = admin_client.get("/api/admin/users").json()["users"]
    demo = [u for u in users if u["email"] == "demo@platform.test"][0]
    detail = admin_client.get(f"/api/admin/users/{demo['id']}").json()
    assert detail["summary"]["open_positions"] > 0
