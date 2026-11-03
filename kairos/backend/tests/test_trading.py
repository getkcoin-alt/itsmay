from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.market import service as mkt
from app.market import sim
from app.models import LedgerEntry, User
from app.trading import engine, portfolio


def _summary(c):
    return c.get("/api/portfolio/summary").json()


def _uid(c):
    return c.get("/api/auth/me").json()["user"]["id"]


def assert_ledger_consistent(db, user_id):
    from app.models import Account

    db.expire_all()
    total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.user_id == user_id))
    acct = db.get(Account, user_id)
    assert Decimal(acct.cash_balance) == Decimal(total)


def test_market_buy_updates_cash_position_and_ledger(user_client, db):
    before = _summary(user_client)
    assert before["cash_balance"] == 1_000_000
    r = user_client.post("/api/orders", json={"symbol": "TCS", "side": "BUY", "quantity": 10})
    assert r.status_code == 201
    o = r.json()["order"]
    assert o["status"] == "FILLED" and o["filled_price"] > 0
    expected_cash = 1_000_000 - 10 * o["filled_price"] - o["fees"]
    after = _summary(user_client)
    assert after["cash_balance"] == pytest.approx(expected_cash, abs=0.01)
    pos = user_client.get("/api/portfolio/positions").json()["positions"]
    assert pos[0]["symbol"] == "TCS" and pos[0]["quantity"] == 10
    assert pos[0]["avg_price"] == pytest.approx(o["filled_price"])
    tx = user_client.get("/api/transactions").json()["transactions"]
    assert {t["type"] for t in tx} >= {"TRADE_BUY", "FEE", "DEPOSIT"}
    assert_ledger_consistent(db, _uid(user_client))


def test_average_price_and_realized_pnl(db, user_client):
    user = db.get(User, _uid(user_client))
    t0 = sim.now_ts() - 5 * 86400
    p1 = engine.place_order(db, user, engine.OrderRequest("INFY", "BUY", quantity=Decimal(10)), at=t0)
    p2 = engine.place_order(db, user, engine.OrderRequest("INFY", "BUY", quantity=Decimal(30)), at=t0 + 86400)
    avg = (Decimal(10) * p1.filled_price + Decimal(30) * p2.filled_price) / 40
    s = engine.place_order(db, user, engine.OrderRequest("INFY", "SELL", quantity=Decimal(15)), at=t0 + 2 * 86400)
    assert s.status == "FILLED"
    expected = (s.filled_price - avg) * 15
    assert float(s.realized_pnl) == pytest.approx(float(expected), abs=0.01)
    pos = [p for p in portfolio.position_rows(db, user) if p["symbol"] == "INFY"][0]
    assert pos["quantity"] == 25
    assert pos["avg_price"] == pytest.approx(float(avg), rel=1e-9)
    assert_ledger_consistent(db, user.id)


def test_insufficient_funds_rejected_without_state_change(user_client, db):
    before = _summary(user_client)
    r = user_client.post("/api/orders", json={"symbol": "MARUTI", "side": "BUY", "quantity": 300})
    o = r.json()["order"]
    assert o["status"] == "REJECTED" and "Insufficient funds" in o["reject_reason"]
    assert _summary(user_client)["cash_balance"] == before["cash_balance"]
    assert user_client.get("/api/portfolio/positions").json()["positions"] == []


def test_cannot_short_sell_stock_or_crypto(user_client):
    for sym, qty in (("ITC", 1), ("BTC", "0.001")):
        o = user_client.post("/api/orders", json={"symbol": sym, "side": "SELL", "quantity": qty}).json()["order"]
        assert o["status"] == "REJECTED" and "Insufficient holdings" in o["reject_reason"]


def test_invalid_quantity_and_symbol(user_client):
    assert user_client.post("/api/orders", json={"symbol": "ITC", "side": "BUY", "quantity": 1.5}).status_code == 422
    assert user_client.post("/api/orders", json={"symbol": "BTC", "side": "BUY", "quantity": "0.00001"}).status_code == 422
    assert user_client.post("/api/orders", json={"symbol": "NOPE", "side": "BUY", "quantity": 1}).status_code == 404
    assert user_client.post("/api/orders", json={"symbol": "NIFTY", "side": "BUY", "quantity": 1}).status_code == 404  # index not tradable
    assert user_client.post("/api/orders", json={"symbol": "ITC", "side": "HOLD", "quantity": 1}).status_code == 422


def test_crypto_fractional_buy(user_client):
    o = user_client.post("/api/orders", json={"symbol": "BTC", "side": "BUY", "quantity": "0.0125"}).json()["order"]
    assert o["status"] == "FILLED" and o["quantity"] == 0.0125


def test_limit_order_rests_reserves_and_cancels(user_client):
    q = user_client.get("/api/market/quote/SBIN").json()["quote"]
    limit = round(q["ltp"] * 0.8, 1)
    o = user_client.post("/api/orders", json={"symbol": "SBIN", "side": "BUY", "order_type": "LIMIT", "quantity": 100, "limit_price": limit}).json()["order"]
    assert o["status"] == "OPEN" and o["reserved_amount"] > 100 * limit * 0.99
    s = _summary(user_client)
    assert s["reserved_for_orders"] == pytest.approx(o["reserved_amount"])
    assert s["available_cash"] == pytest.approx(s["cash_balance"] - o["reserved_amount"])
    r = user_client.delete(f"/api/orders/{o['id']}")
    assert r.json()["order"]["status"] == "CANCELLED"
    assert _summary(user_client)["reserved_for_orders"] == 0
    assert user_client.delete(f"/api/orders/{o['id']}").status_code == 409


def test_marketable_limit_fills_immediately(user_client):
    q = user_client.get("/api/market/quote/ITC").json()["quote"]
    o = user_client.post("/api/orders", json={"symbol": "ITC", "side": "BUY", "order_type": "LIMIT", "quantity": 5, "limit_price": round(q["ltp"] * 1.05, 2)}).json()["order"]
    assert o["status"] == "FILLED" and o["filled_price"] <= o["limit_price"]


def test_limit_order_fills_when_price_reaches_limit(db, user_client):
    user = db.get(User, _uid(user_client))
    spec = mkt.get_spec(db, "WIPRO")
    t0 = sim.now_ts() - 3 * 86400
    p0 = mkt.spec_price(spec, t0)
    # Find the minimum over the next two days and set a limit just above it.
    lows = [(mkt.spec_price(spec, t0 + k * 600), t0 + k * 600) for k in range(1, 288)]
    low, t_low = min(lows)
    limit = Decimal(str(round(low + (p0 - low) * 0.25, 2)))
    assert float(limit) < p0
    o = engine.place_order(db, user, engine.OrderRequest("WIPRO", "BUY", "LIMIT", quantity=Decimal(50), limit_price=limit), at=t0)
    assert o.status == "OPEN"
    assert engine.match_open_orders(db, user.id, at=t_low) == 1
    db.refresh(o)
    assert o.status == "FILLED" and o.filled_price <= limit and float(o.reserved_amount) == 0
    assert_ledger_consistent(db, user.id)


def test_idempotent_client_order_id(user_client):
    body = {"symbol": "NTPC", "side": "BUY", "quantity": 3, "client_order_id": "abc-123"}
    a = user_client.post("/api/orders", json=body).json()["order"]
    b = user_client.post("/api/orders", json=body).json()["order"]
    assert a["id"] == b["id"]
    assert len([o for o in user_client.get("/api/orders").json()["orders"] if o["symbol"] == "NTPC"]) == 1


def _option_symbol(c, kind="CE", offset=2):
    und = c.get("/api/market/options/underlyings").json()["underlyings"]
    nifty = [u for u in und if u["symbol"] == "NIFTY"][0]
    chain = c.get("/api/market/options/chain/NIFTY", params={"expiry": nifty["expiries"][1][:10], "strikes": 6}).json()
    atm_idx = [i for i, r in enumerate(chain["rows"]) if r["strike"] == chain["atm_strike"]][0]
    return chain["rows"][atm_idx + offset][kind]


def test_option_buy_then_sell_close(user_client, db):
    ce = _option_symbol(user_client)
    pv = user_client.post("/api/orders/preview", json={"symbol": ce["symbol"], "side": "BUY", "lots": 2}).json()
    assert pv["can_execute"] and pv["quantity"] == 150 and pv["margin_required"] == 0
    assert any(w["code"] == "PREMIUM_AT_RISK" for w in pv["warnings"])
    o = user_client.post("/api/orders", json={"symbol": ce["symbol"], "side": "BUY", "lots": 2}).json()["order"]
    assert o["status"] == "FILLED" and o["lots"] == 2
    o2 = user_client.post("/api/orders", json={"symbol": ce["symbol"], "side": "SELL", "lots": 2}).json()["order"]
    assert o2["status"] == "FILLED"
    assert user_client.get("/api/portfolio/positions").json()["positions"] == []
    assert_ledger_consistent(db, _uid(user_client))


def test_short_call_blocks_margin_and_warns_unlimited_loss(user_client, db):
    ce = _option_symbol(user_client, "CE", 3)
    pv = user_client.post("/api/orders/preview", json={"symbol": ce["symbol"], "side": "SELL", "lots": 1}).json()
    assert pv["can_execute"]
    assert pv["margin_required"] > 50_000
    assert any(w["code"] == "UNLIMITED_LOSS" for w in pv["warnings"])
    o = user_client.post("/api/orders", json={"symbol": ce["symbol"], "side": "SELL", "lots": 1}).json()["order"]
    assert o["status"] == "FILLED"
    s = _summary(user_client)
    assert s["margin_blocked"] == pytest.approx(pv["margin_required"], rel=0.01)
    assert s["available_cash"] == pytest.approx(s["cash_balance"] - s["margin_blocked"], abs=0.01)
    pos = user_client.get("/api/portfolio/positions").json()["positions"][0]
    assert pos["side"] == "SHORT" and pos["quantity"] == -75
    # Buy back releases margin.
    user_client.post("/api/orders", json={"symbol": ce["symbol"], "side": "BUY", "lots": 1})
    assert _summary(user_client)["margin_blocked"] == 0


def test_short_put_warning(user_client):
    pe = _option_symbol(user_client, "PE", -3)
    pv = user_client.post("/api/orders/preview", json={"symbol": pe["symbol"], "side": "SELL", "lots": 1}).json()
    assert any(w["code"] == "LARGE_LOSS" for w in pv["warnings"])


def test_option_expiry_settlement(db, user_client):
    user = db.get(User, _uid(user_client))
    spec = mkt.get_spec(db, "NIFTY")
    exp = mkt.expiries_for(spec)[0]
    t_buy = exp.timestamp() - 3600
    strike = round(mkt.spec_price(spec, t_buy) / 50) * 50 - 200  # ITM call
    sym = mkt.option_symbol("NIFTY", exp, strike, "CE")
    o = engine.place_order(db, user, engine.OrderRequest(sym, "BUY", lots=1), at=t_buy)
    assert o.status == "FILLED"
    assert engine.settle_expiries(db, user.id, at=exp.timestamp() + 60) == 1
    intrinsic = max(0.0, mkt.spec_price(spec, exp.timestamp()) - strike)
    rows = portfolio.position_rows(db, user, include_closed=True)
    pos = [r for r in rows if r["symbol"] == sym][0]
    assert pos["quantity"] == 0
    entry = db.scalar(select(LedgerEntry).where(LedgerEntry.user_id == user.id, LedgerEntry.type == "EXPIRY_SETTLEMENT"))
    assert float(entry.amount) == pytest.approx(round(intrinsic / 0.05) * 0.05 * 75, abs=0.5)
    assert_ledger_consistent(db, user.id)


def test_forex_long_close_settles_pnl(db, user_client):
    user = db.get(User, _uid(user_client))
    t0 = sim.now_ts() - 2 * 86400
    o = engine.place_order(db, user, engine.OrderRequest("EURUSD", "BUY", lots=2), at=t0)
    assert o.status == "FILLED" and float(o.quantity) == 2000
    s = portfolio.summary(db, user)
    assert s["margin_blocked"] > 0
    c = engine.place_order(db, user, engine.OrderRequest("EURUSD", "SELL", lots=2), at=t0 + 86400)
    usdinr = mkt.fx_to_inr(db, "USD", t0 + 86400)
    expected = (float(c.filled_price) - float(o.filled_price)) * 2000 * usdinr
    assert float(c.realized_pnl) == pytest.approx(expected, abs=0.05)
    s2 = portfolio.summary(db, user)
    assert s2["margin_blocked"] == 0
    fx_pnl = db.scalar(select(LedgerEntry).where(LedgerEntry.user_id == user.id, LedgerEntry.type == "FX_PNL"))
    assert float(fx_pnl.amount) == pytest.approx(float(c.realized_pnl), abs=0.01)
    assert_ledger_consistent(db, user.id)


def test_forex_short_allowed(user_client):
    o = user_client.post("/api/orders", json={"symbol": "USDJPY", "side": "SELL", "lots": 1}).json()["order"]
    assert o["status"] == "FILLED"
    pos = user_client.get("/api/portfolio/positions").json()["positions"][0]
    assert pos["side"] == "SHORT" and pos["currency"] == "JPY"


def test_equity_replay_matches_summary(db, user_client):
    user_client.post("/api/orders", json={"symbol": "RELIANCE", "side": "BUY", "quantity": 20})
    user_client.post("/api/orders", json={"symbol": "ETH", "side": "BUY", "quantity": "0.2"})
    user_client.post("/api/orders", json={"symbol": "USDINR", "side": "BUY", "lots": 1})
    user = db.get(User, _uid(user_client))
    s = portfolio.summary(db, user)
    replay = portfolio.equity_at_times(db, user.id, [sim.now_ts()])[0][1]
    assert replay == pytest.approx(s["total_value"], rel=1e-3)
    assert s["overall_pnl"] == pytest.approx(s["total_value"] - s["net_deposits"])


def test_demo_account_seed_is_consistent(db):
    user = db.scalar(select(User).where(User.email == "demo@platform.test"))
    assert_ledger_consistent(db, user.id)
    s = portfolio.summary(db, user)
    classes = {r["asset_class"] for r in portfolio.position_rows(db, user)}
    assert classes == {"stock", "crypto", "forex", "option"}
    assert s["net_deposits"] == 1_025_000
    assert s["available_cash"] > 150_000  # room to demo option selling


def test_wallet_deposit_withdraw(user_client, db):
    r = user_client.post("/api/wallet/deposit", json={"amount": 50000, "method": "SIM_UPI"})
    assert r.status_code == 200 and r.json()["balance"] == 1_050_000
    assert user_client.post("/api/wallet/withdraw", json={"amount": 2_000_000}).status_code == 422
    assert user_client.post("/api/wallet/withdraw", json={"amount": 10000}).json()["balance"] == 1_040_000
    assert user_client.post("/api/wallet/deposit", json={"amount": 99_999_999, "method": "SIM_UPI"}).status_code == 422
    assert user_client.post("/api/wallet/deposit", json={"amount": -5, "method": "SIM_UPI"}).status_code == 422
    assert_ledger_consistent(db, _uid(user_client))


def test_reset_demo_account(user_client):
    user_client.post("/api/orders", json={"symbol": "TCS", "side": "BUY", "quantity": 5})
    r = user_client.post("/api/profile/reset-demo").json()
    assert r["account"]["cash_balance"] == 1_000_000
    assert user_client.get("/api/orders").json()["orders"] == []


def test_watchlist(user_client):
    assert user_client.post("/api/watchlist", json={"symbol": "btc"}).status_code == 201
    assert [i["symbol"] for i in user_client.get("/api/watchlist").json()["items"]] == ["BTC"]
    assert user_client.delete("/api/watchlist/BTC").status_code == 200
    assert user_client.post("/api/watchlist", json={"symbol": "ZZZ"}).status_code == 404


def test_deterministic_prices():
    a = sim.raw_price("TCS", 3046.2, 0.22, 1_790_000_000)
    b = sim.raw_price("TCS", 3046.2, 0.22, 1_790_000_000)
    assert a == b
    assert sim.raw_price("TCS", 3046.2, 0.22, sim.ANCHOR) == pytest.approx(3046.2)
