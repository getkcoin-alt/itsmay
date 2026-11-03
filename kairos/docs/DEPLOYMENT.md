# Deployment

## Status

**Not deployed.** The build session found no suitable existing target:

- The connected Vercel team has no project for this app.
- The only Supabase project belongs to a different application and is inactive.
- Railway (used by other services in this repository) needs authorisation that wasn't available.
- Docker images could not be built in the build sandbox (no Docker daemon). The runtime paths the images use were verified directly: Next.js standalone server, fresh-database `alembic upgrade` → seed → serve.

No new billable cloud resources were created without approval. Pick one of the options below.

## Environment variables

| Variable | Tier | Default | Production guidance |
|---|---|---|---|
| `KAIROS_DATABASE_URL` | API | local Postgres | `postgresql+psycopg://user:pass@host:5432/db` |
| `KAIROS_REDIS_URL` | API | unset (in-memory limits) | Set for multi-instance rate limiting |
| `KAIROS_ADMIN_PASSWORD` | API | `Admin@123456` (local only) | **Required**: strong secret, delivered privately |
| `KAIROS_DEMO_USER_PASSWORD` | API | `Demo@123456` | Keep (public demo credential) |
| `KAIROS_COOKIE_SECURE` | API | `false` | `true` behind HTTPS |
| `KAIROS_EXPOSE_RESET_LINKS` | API | `true` | `false` on shared deployments |
| `KAIROS_BACKGROUND_MATCHER` | API | `true` | Run on exactly one long-lived instance (or `false` on serverless; matching also runs on requests) |
| `KAIROS_CORS_ORIGINS` | API | empty | Leave empty when the web app proxies `/api` |
| `KAIROS_API_ORIGIN` | Web (build and runtime) | `http://127.0.0.1:8000` | Internal URL of the API |
| `NEXT_PUBLIC_WS_URL` | Web (build) | same origin | Public `wss://` origin of the API if you want WebSocket streaming |

Secrets belong in the platform's secret store, never in the repository. `.env` is git-ignored.

## Option A: single host with Docker Compose (simplest)

Any VM with Docker (for example the existing self-hosted VPS used by other projects):

```bash
git clone <repo> && cd itsmay/kairos
cp .env.example .env              # set KAIROS_ADMIN_PASSWORD and POSTGRES_PASSWORD
docker compose up -d --build      # api migrates and seeds on start
curl -s localhost:3000/api/health
```

Put a TLS reverse proxy (Caddy or nginx) in front of port 3000, then set `KAIROS_COOKIE_SECURE=true`. To enable WebSocket streaming, also route `/api/market/ws` straight to `api:8000` with upgrade headers.

## Option B: Railway (matches the repository's existing infra)

1. Create a project and add the **PostgreSQL** and **Redis** plugins.
2. Add an **API** service from this repository with root directory `kairos/backend` (Dockerfile build). Set the variables `KAIROS_DATABASE_URL`, `KAIROS_REDIS_URL`, `KAIROS_ADMIN_PASSWORD`, `KAIROS_COOKIE_SECURE=true` and `KAIROS_EXPOSE_RESET_LINKS=false`. Health check path: `/api/health`.
3. Add a **web** service with root directory `kairos/frontend` (Dockerfile) and build arg/env `KAIROS_API_ORIGIN=http://<api-service>.railway.internal:8000`. Expose it publicly.

## Option C: Vercel (web) + managed Postgres + API container

Vercel hosts the Next.js app well, but the long-lived API (background matcher, WebSocket) belongs on a container host such as Railway, Fly.io, Render or a VPS. Point `KAIROS_API_ORIGIN` at that API's public HTTPS URL. Use a managed Postgres (Neon, Supabase or RDS) for `KAIROS_DATABASE_URL`.

## Post-deploy verification checklist

```bash
curl -s https://<host>/api/health                       # {"status":"ok","database":true,...}
BASE_URL=https://<host> ADMIN_PASSWORD=<secret> node frontend/e2e/demo-walkthrough.cjs
```

The walkthrough logs in as the demo user, trades every asset class, checks RBAC and signs in as admin. It places real simulated orders, so re-seed afterwards: `python -m app.seed --reset` (or reset from Profile).
