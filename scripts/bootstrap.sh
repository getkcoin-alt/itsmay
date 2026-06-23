#!/usr/bin/env bash
# Vault Zeta — first-time setup. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

# ── 1. Find a Python >=3.11 ────────────────────────────────────────
PY=""
for candidate in python3.15 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    ver=$(python3 -c 'import sys; print("%d.%d"%sys.version_info[:2])')
    major=${ver%%.*}; minor=${ver##*.}
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
      PY="$(command -v python3)"
    fi
  fi
fi
if [ -z "$PY" ]; then
  echo "✗ Python 3.11+ not found. Install one:"
  echo "    brew install python@3.12"
  echo "  Then re-run this script."
  exit 1
fi
echo "▶ Using $PY ($($PY --version))"

# ── 2. .env ────────────────────────────────────────────────────────
echo "▶ Creating .env if missing"
[ -f .env ] || cp .env.example .env

# ── 3. venv (rebuild if it was created with the wrong python) ─────
if [ -d .venv ]; then
  current_ver=$(.venv/bin/python -c 'import sys; print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "0.0")
  target_ver=$($PY -c 'import sys; print("%d.%d"%sys.version_info[:2])')
  if [ "$current_ver" != "$target_ver" ]; then
    echo "▶ Rebuilding .venv ($current_ver → $target_ver)"
    rm -rf .venv
  fi
fi
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck source=/dev/null
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

# ── 4. Postgres + Redis ───────────────────────────────────────────
# Docker Desktop on macOS sometimes doesn't symlink its CLI into /usr/local/bin.
# Find docker wherever it actually lives.
if ! command -v docker >/dev/null 2>&1; then
  for d in /Applications/Docker.app/Contents/Resources/bin "$HOME/.docker/bin"; do
    if [ -x "$d/docker" ]; then
      export PATH="$d:$PATH"
      echo "▶ Added $d to PATH"
      break
    fi
  done
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "✗ Docker CLI not found. Install Docker Desktop and re-run."
  exit 1
fi

# Wait for the Docker daemon to be ready (Desktop may still be spinning up).
echo "▶ Waiting for Docker daemon"
tries=0
until docker info >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -gt 60 ]; then
    echo "✗ Docker daemon never came up. Is Docker Desktop running?"
    exit 1
  fi
  sleep 1
done

echo "▶ Starting Postgres + Redis"
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
echo "▶ Waiting for Postgres to be healthy"
until docker compose -f infra/docker/docker-compose.yml exec -T postgres pg_isready -U "${POSTGRES_USER:-vault}" >/dev/null 2>&1; do
  sleep 1
done

# ── 5. Ollama models ──────────────────────────────────────────────
echo "▶ Pulling Ollama models (chat + embedding)"
bash scripts/pull_models.sh

echo
echo "✓ Bootstrap complete."
echo "  Run the API:  source .venv/bin/activate && uvicorn apps.api.main:app --reload"
echo "  Smoke test:   curl -N -X POST localhost:8000/v1/chat -H 'content-type: application/json' \\"
echo "                  -d '{\"message\":\"who are you?\"}'"
