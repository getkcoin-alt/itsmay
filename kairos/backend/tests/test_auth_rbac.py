def test_demo_login_and_me(client):
    r = client.post("/api/auth/login", json={"email": "demo@platform.test", "password": "Demo@123456"})
    assert r.status_code == 200
    assert "kairos_session" in r.cookies
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "demo@platform.test" and me["role"] == "user"
    assert "password_hash" not in me


def test_wrong_password_rejected(client):
    r = client.post("/api/auth/login", json={"email": "demo@platform.test", "password": "nope"})
    assert r.status_code == 401


def test_login_rate_limited(client):
    codes = [client.post("/api/auth/login", json={"email": "x@platform.test", "password": "bad"}).status_code for _ in range(10)]
    assert 429 in codes


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={"full_name": "W", "email": "weak@example.test", "password": "password", "accept_terms": True})
    assert r.status_code == 422


def test_duplicate_registration(client):
    r = client.post("/api/auth/register", json={"full_name": "Dup", "email": "demo@platform.test", "password": "Str0ng!Pass", "accept_terms": True})
    assert r.status_code == 409


def test_unauthenticated_blocked(client):
    assert client.get("/api/portfolio/summary").status_code == 401
    assert client.get("/api/market/instruments").status_code == 401


def test_csrf_header_required(user_client):
    r = user_client.post("/api/orders", json={"symbol": "ITC", "side": "BUY", "quantity": 1}, headers={"x-kairos-client": ""})
    assert r.status_code == 403


def test_user_cannot_access_admin(user_client):
    for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/settings", "/api/admin/audit-logs"):
        assert user_client.get(path).status_code == 403
    assert user_client.patch("/api/admin/assets/BTC", json={"enabled": False}).status_code == 403


def test_admin_access(admin_client):
    s = admin_client.get("/api/admin/stats").json()
    assert s["users_total"] >= 9


def test_logout_revokes_session(user_client):
    assert user_client.get("/api/auth/me").status_code == 200
    user_client.post("/api/auth/logout")
    assert user_client.get("/api/auth/me").status_code == 401


def test_password_reset_flow(client, user_client):
    email = user_client.get("/api/auth/me").json()["user"]["email"]
    r = client.post("/api/auth/forgot-password", json={"email": email}).json()
    token = r["demo_reset_link"].split("token=")[1]
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "N3w!Password"}).status_code == 200
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "N3w!Password"}).status_code == 400
    assert client.post("/api/auth/login", json={"email": email, "password": "N3w!Password"}).status_code == 200


def test_seeded_accounts_cannot_be_reset(client):
    r = client.post("/api/auth/forgot-password", json={"email": "admin@platform.test"}).json()
    assert "demo_reset_link" not in r


def test_disabled_user_cannot_login(admin_client, client):
    c = client
    c.post("/api/auth/register", json={"full_name": "To Disable", "email": "disable.me@example.test", "password": "Str0ng!Pass", "accept_terms": True})
    users = admin_client.get("/api/admin/users", params={"q": "disable.me"}).json()["users"]
    uid = users[0]["id"]
    assert admin_client.patch(f"/api/admin/users/{uid}", json={"status": "disabled"}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401  # session revoked
    assert c.post("/api/auth/login", json={"email": "disable.me@example.test", "password": "Str0ng!Pass"}).status_code == 403
