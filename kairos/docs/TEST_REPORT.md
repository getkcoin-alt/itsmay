# Test report

Run date: 2026-11-03. Environment: build container with PostgreSQL 16, Redis 7, Python 3.13, Node 22, Chromium (Playwright).

## Summary

| Check | Result |
|---|---|
| Backend unit/integration tests (`pytest`, against PostgreSQL) | **42 passed**, 0 failed |
| Backend lint (`ruff`) | clean |
| Frontend type check (`tsc --noEmit`, strict) | clean |
| Frontend production build (`next build`) | success, 29 routes |
| Database migration on an empty database + seed, run twice | success; second run idempotent |
| Next.js standalone server (Docker runtime path) | serves pages and proxies the API |
| WebSocket quote stream (direct and via the web proxy) | delivers simulated quotes |
| End-to-end demo walkthrough (Playwright, desktop 1440×900 and iPhone 13) | **20/20 steps passed, 0 page errors, 0 console errors, 0 HTTP 5xx** |
| Mobile horizontal overflow check | none |
| Docker image build | **not run**: no Docker daemon available in the build sandbox |
| Deployed-URL verification | **not run**: not deployed (see DEPLOYMENT.md) |

## Backend coverage (tests/)

- **Auth and RBAC** (`test_auth_rbac.py`): demo login, wrong password, login rate limit, password policy, duplicate email, unauthenticated access blocked, CSRF header required, users get 403 on every admin route, admin access, logout revokes the session, full password-reset flow (single-use token), seeded accounts excluded from reset, disabling a user revokes sessions and blocks login.
- **Trading engine** (`test_trading.py`):
  - market buy updates cash, position and ledger exactly
  - weighted average cost and realised P&L
  - insufficient funds rejected without state change
  - no short selling of stock or crypto
  - quantity, lot, symbol and side validation
  - fractional crypto
  - limit order reserves funds, cancel releases them, double cancel returns 409
  - marketable limit fills immediately
  - resting limit fills when the simulated path crosses the limit
  - idempotent `client_order_id`
  - option buy then close
  - short call blocks SPAN-style margin, shows the *unlimited loss* warning, and buying back releases margin
  - short put *large loss* warning
  - option expiry cash settlement at intrinsic
  - forex long/close settles converted P&L and releases margin
  - forex short
  - replayed equity equals live summary
  - seeded demo account is consistent (ledger = cash, all four asset classes, free cash available)
  - wallet deposit/withdraw limits and validation
  - demo reset
  - watchlist
  - price determinism
  - Every money-moving test asserts the invariant `cash_balance == Σ ledger`.
- **Admin** (`test_admin.py`): disabling an instrument blocks orders, price override flows to quotes and fills (with bounds), fee configuration changes charged fees (with validation), trading halt and INR-pairs-only jurisdiction mode, KYC submit and approve (only PENDING can be reviewed; PAN stored masked), audit log records admin actions, reports and listings.

## End-to-end walkthrough output

`frontend/e2e/demo-walkthrough.cjs` covers the 15-step client demo story: login → dashboard → stocks → stock detail → buy → option chain → buy option → write option (with risk acknowledgement) → crypto → buy BTC → forex → short EUR/USD → portfolio and positions → orders and transactions → wallet deposit → RBAC denial for users → admin console (all eight admin pages) → mobile dashboard, chain and order ticket.

    ✓ 01 landing page renders (346ms)
    ✓ 02 login with demo account (2263ms)
    ✓ 03 stock market list (580ms)
    ✓ 04 stock detail + chart (3034ms)
    ✓ 05 buy stock (market order) (898ms)
    ✓ 06 options chain (1085ms)
    ✓ 07 buy an option (long call) (1456ms)
    ✓ 08 sell (write) an option with risk acknowledgement (1287ms)
    ✓ 09 crypto market (463ms)
    ✓ 10 buy Bitcoin (2043ms)
    ✓ 11 forex market (370ms)
    ✓ 12 open a currency position (short EUR/USD) (1866ms)
    ✓ 13 portfolio (1444ms)
    ✓ 13b positions (1249ms)
    ✓ 14 orders + transactions (676ms)
    ✓ 14c wallet deposit (980ms)
    ✓ 14d other user pages render (771ms)
    ✓ 14e normal user is blocked from admin (143ms)
    ✓ 15 admin dashboard (5878ms)
    ✓ 16 mobile: dashboard, options, stock ticket (5944ms)
    
    20/20 steps passed; 0 runtime problems

## Defects found and fixed during QA

1. The order review modal rendered inside the sticky ticket column, so the price-chart canvas could paint over it and block **Confirm**. Fixed by portalling all modals to `<body>`.
2. The allocation legend overflowed narrow cards, and portfolio stat tiles truncated large values. Layout fixed.
3. The performance chart included deposit and withdrawal jumps. It now plots trading P&L (equity − net deposits).
4. Server messages used Western digit grouping. Switched to Indian grouping (₹19,30,796).
5. The seeded demo account had too little free cash to demonstrate option writing. The seed book was rebalanced (about ₹2.9 L available).
6. The admin "initial demo balance" setting was ignored for new or reset accounts. Now applied.

## How to re-run

```bash
cd backend && .venv/bin/pytest -q                      # needs a kairos_test database (see tests/conftest.py)
cd frontend && npx tsc --noEmit && npm run build
node e2e/demo-walkthrough.cjs [screenshotDir]          # with API on :8000 and web on :3000
cd backend && .venv/bin/python -m app.seed --reset     # restore the demo account afterwards
```
