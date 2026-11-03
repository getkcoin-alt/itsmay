# API reference

Base path `/api`. JSON in and out. Interactive Swagger UI: `/api/docs`. Full schema: [`openapi.json`](openapi.json).

**Auth.** `POST /api/auth/login` sets an httpOnly `kairos_session` cookie and also returns `token`. Native clients send `Authorization: Bearer <token>` instead.
**CSRF.** Cookie-authenticated non-GET requests must include the header `X-Kairos-Client: <any value>`.
**Errors.** `{"detail": "..."}` (or a Pydantic list for 422). Codes: 401 unauthenticated, 403 forbidden or CSRF, 404 unknown, 409 state conflict, 422 validation, 429 rate-limited.
**Simulation.** Market, portfolio and order payloads include `"simulated": true`.

## Auth

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/auth/register` | `full_name, email, password, phone?, accept_terms` | Creates user, account and simulated KYC record; credits the initial demo balance; logs in |
| POST | `/auth/login` | `email, password` | Rate limited |
| POST | `/auth/logout` | – | Revokes session |
| GET | `/auth/me` | – | Current user (with KYC) |
| POST | `/auth/forgot-password` | `email` | Always 200. Returns `demo_reset_link` only when `KAIROS_EXPOSE_RESET_LINKS=true`. Shared demo accounts are excluded |
| POST | `/auth/reset-password` | `token, password` | Single use, 30 min; revokes all sessions |
| POST | `/auth/change-password` | `current_password, new_password` | Not allowed for shared demo accounts |

## Market data (simulated)

| Method | Path | Notes |
|---|---|---|
| GET | `/market/instruments?asset_class=stock\|crypto\|forex\|index&q=` | Quotes for enabled instruments |
| GET | `/market/overview` | Indices, gainers, losers, most active, crypto movers, forex, breadth |
| GET | `/market/quote/{symbol}` | Equity, crypto, FX or index quote (with `about`), or an option contract quote |
| GET | `/market/candles/{symbol}?range=1D\|1W\|1M\|3M\|6M\|1Y\|5Y` | OHLCV. Option symbols return premium bars |
| GET | `/market/options/underlyings` | Spot, lot size, strike step and expiries |
| GET | `/market/options/chain/{underlying}?expiry=YYYY-MM-DD&strikes=4..25` | CE/PE rows: LTP, change, IV, Greeks, OI, OI change, volume; ATM, PCR |
| WS | `/market/ws` | Send `{"subscribe":["RELIANCE","BTC",...]}`; receive `{"type":"quotes","data":{...}}` every 2 s. Cookie or `?token=` auth |

## Trading

`OrderIn`: `symbol`, `side` (BUY/SELL), `order_type` (MARKET/LIMIT), `quantity` (units; stocks and crypto) **or** `lots` (options and forex), `limit_price?`, `client_order_id?` (idempotency key).

| Method | Path | Notes |
|---|---|---|
| POST | `/orders/preview` | No state change. Returns price, notional (INR), fees, margin required or released, available before and after, position after, realised P&L, `warnings[]`, `can_execute`, `rejection_reason` |
| POST | `/orders` | Executes (FILLED), rests (OPEN) or records a rejection (REJECTED). Returns `{order, account}` |
| GET | `/orders?status=&asset_class=&limit=` | Order book with status counts |
| DELETE | `/orders/{id}` | Cancel an OPEN order and release its reservation (409 otherwise) |

## Portfolio, wallet, profile

| Method | Path | Notes |
|---|---|---|
| GET | `/portfolio/summary` | Cash, available, margin (blocked and live), reserved, holdings, invested, total value, day/overall/realised/unrealised P&L, fees, allocation, risk level |
| GET | `/portfolio/positions?include_closed=` | Per position: avg, LTP, invested, market value, unrealised and day P&L, margin, option Greeks and days to expiry |
| GET | `/portfolio/performance?range=1D\|1W\|1M\|3M\|ALL` | Replayed equity, net deposits and P&L series |
| GET | `/dashboard` | Summary, positions, top performers and recent orders in one call |
| GET/POST/DELETE | `/watchlist`, `/watchlist/{symbol}` | Up to 50 instruments |
| GET | `/wallet` | Balances, totals and simulated funding methods |
| POST | `/wallet/deposit` | `amount, method` (SIM_UPI, SIM_NETBANKING, SIM_BANK). Per-deposit and balance caps |
| POST | `/wallet/withdraw` | `amount` ≤ available (cash − margin − reservations) |
| GET | `/transactions?type=A,B&limit=` | Cash ledger |
| GET/PATCH | `/profile` | `full_name, phone, preferences` |
| POST | `/profile/kyc` | Simulated KYC. Dummy PAN format; stored masked only |
| POST | `/profile/reset-demo` | Wipes activity; demo user re-seeded, others re-credited |

## Admin (role = admin)

| Method | Path | Notes |
|---|---|---|
| GET | `/admin/stats` | Users, active users, KYC pending, orders, positions, activity by class, fees |
| GET | `/admin/reports?days=7..120` | Daily orders, fills, rejections, fees and sign-ups; top instruments; ledger flows |
| GET | `/admin/users?q=&status=` · `/admin/users/{id}` | List and detail (summary, positions, orders, ledger) |
| PATCH | `/admin/users/{id}` | `status` (active/disabled, revokes sessions), `role` |
| POST | `/admin/users/{id}/reset` | Reset a user's demo account |
| GET | `/admin/kyc?status=` · POST `/admin/kyc/{user_id}` | Review queue and approve/reject (`decision, note`) |
| GET | `/admin/assets` · PATCH `/admin/assets/{symbol}` | `enabled`, `target_price` (0.2×–5× model), `clear_override`, `volatility` |
| GET | `/admin/orders` · `/admin/transactions` | Platform-wide, filterable |
| GET/PUT | `/admin/settings` | `fees.{stock,option,crypto,forex}.{pct,cap,flat}`, `trading.{forex_leverage,max_order_value_inr,require_kyc_for_trading,forex_inr_pairs_only,options_selling_enabled,trading_halted}`, `wallet.{max_single_deposit,max_balance,initial_demo_balance}` |
| GET | `/admin/audit-logs?action=&limit=` | Audit trail |

## Example

```bash
B=http://localhost:3000/api; H='-H x-kairos-client:cli -H content-type:application/json'
curl -c c.txt $H -X POST $B/auth/login -d '{"email":"demo@platform.test","password":"Demo@123456"}'
curl -b c.txt $H -X POST $B/orders/preview -d '{"symbol":"NIFTY-20261110-26150-CE","side":"SELL","lots":1}'
curl -b c.txt $H -X POST $B/orders -d '{"symbol":"BTC","side":"BUY","quantity":"0.002","client_order_id":"demo-1"}'
```
