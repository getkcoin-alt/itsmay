# Deployment

## Status: live on Vercel

| Component | Where |
|---|---|
| Web app (Next.js) | https://scriza-trade.vercel.app (Vercel project `scriza-trade`, root `scriza-trade/frontend`) |
| API (FastAPI, Python functions) | https://scriza-trade-api.vercel.app (Vercel project `scriza-trade-api`, root `scriza-trade/backend`) |
| Database | Supabase Postgres `kairos-markets-demo` (internal name kept from the original build) (ap-south-1), private schema `kairos`, owned by role `kairos_app`, reached through the transaction pooler |

Both Vercel projects are connected to `getkcoin-alt/itsmay`. The web app proxies `/api/*` to the API (`SCRIZA_API_ORIGIN`).

Serverless specifics:

- `SCRIZA_DB_SERVERLESS=true`: NullPool, no prepared statements (Supavisor).
- `SCRIZA_DB_SCHEMA=kairos`: tables are kept out of Supabase's auto-generated public REST API.
- `SCRIZA_BACKGROUND_MATCHER=false`: limit orders and expiries are processed on each user's requests.
- Rate limiting is in-memory per function instance (no Redis).
- The WebSocket stream isn't available on Vercel, so the UI polls instead.

Seeding or resetting the demo data (no shell on Vercel) uses a token-gated endpoint:

```bash
curl -X POST -H "x-scriza-client: ops" -H "x-seed-token: $SCRIZA_SEED_TOKEN" "https://scriza-trade-api.vercel.app/api/internal/seed?reset=1"
```

The token, admin password and database password are stored only as *sensitive* Vercel environment variables. Unset `SCRIZA_SEED_TOKEN` to disable the endpoint.

## Environment variables

| Variable | Tier | Default | Production guidance |
|---|---|---|---|
| `SCRIZA_DATABASE_URL` | API | local Postgres | `postgresql+psycopg://user:pass@host:5432/db` |
| `SCRIZA_REDIS_URL` | API | unset (in-memory limits) | Set for multi-instance rate limiting |
| `SCRIZA_ADMIN_PASSWORD` | API | `Admin@123456` (local only) | **Required**: strong secret, delivered privately |
| `SCRIZA_DEMO_USER_PASSWORD` | API | `Demo@123456` | Keep (public demo credential) |
| `SCRIZA_COOKIE_SECURE` | API | `false` | `true` behind HTTPS |
| `SCRIZA_EXPOSE_RESET_LINKS` | API | `true` | `false` on shared deployments |
| `SCRIZA_BACKGROUND_MATCHER` | API | `true` | Run on exactly one long-lived instance (or `false` on serverless; matching also runs on requests) |
| `SCRIZA_CORS_ORIGINS` | API | empty | Leave empty when the web app proxies `/api` |
| `SCRIZA_API_ORIGIN` | Web (build and runtime) | `http://127.0.0.1:8000` | Internal URL of the API |
| `NEXT_PUBLIC_WS_URL` | Web (build) | same origin | Public `wss://` origin of the API if you want WebSocket streaming |

Secrets belong in the platform's secret store, never in the repository. `.env` is git-ignored.

## Option A: single host with Docker Compose (simplest)

Any VM with Docker (for example the existing self-hosted VPS used by other projects):

```bash
git clone <repo> && cd itsmay/scriza-trade
cp .env.example .env              # set SCRIZA_ADMIN_PASSWORD and POSTGRES_PASSWORD
docker compose up -d --build      # api migrates and seeds on start
curl -s localhost:3000/api/health
```

Put a TLS reverse proxy (Caddy or nginx) in front of port 3000, then set `SCRIZA_COOKIE_SECURE=true`. To enable WebSocket streaming, also route `/api/market/ws` straight to `api:8000` with upgrade headers.

## Option B: Railway (matches the repository's existing infra)

1. Create a project and add the **PostgreSQL** and **Redis** plugins.
2. Add an **API** service from this repository with root directory `scriza-trade/backend` (Dockerfile build). Set the variables `SCRIZA_DATABASE_URL`, `SCRIZA_REDIS_URL`, `SCRIZA_ADMIN_PASSWORD`, `SCRIZA_COOKIE_SECURE=true` and `SCRIZA_EXPOSE_RESET_LINKS=false`. Health check path: `/api/health`.
3. Add a **web** service with root directory `scriza-trade/frontend` (Dockerfile) and build arg/env `SCRIZA_API_ORIGIN=http://<api-service>.railway.internal:8000`. Expose it publicly.

## Option C: Vercel (web) + managed Postgres + API container

Vercel hosts the Next.js app well, but the long-lived API (background matcher, WebSocket) belongs on a container host such as Railway, Fly.io, Render or a VPS. Point `SCRIZA_API_ORIGIN` at that API's public HTTPS URL. Use a managed Postgres (Neon, Supabase or RDS) for `SCRIZA_DATABASE_URL`.

## Post-deploy verification checklist

```bash
curl -s https://<host>/api/health                       # {"status":"ok","database":true,...}
BASE_URL=https://<host> ADMIN_PASSWORD=<secret> node frontend/e2e/demo-walkthrough.cjs
```

The walkthrough logs in as the demo user, trades every asset class, checks RBAC and signs in as admin. It places real simulated orders, so re-seed afterwards: `python -m app.seed --reset` (or reset from Profile).
