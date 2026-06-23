#!/usr/bin/env bash
# Pulls the LLM + embedding models from Ollama. Assumes ollama is installed locally
# OR set OLLAMA_HOST to a remote instance.
set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
CHAT_MODEL="${OLLAMA_MODEL:-gemma3:latest}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"

pull() {
  local model="$1"
  echo "▶ Pulling $model"
  curl -fsS -X POST "$OLLAMA_HOST/api/pull" \
    -H "content-type: application/json" \
    -d "{\"name\":\"$model\",\"stream\":false}" \
    | tail -c 400 || true
  echo
}

pull "$CHAT_MODEL"
pull "$EMBED_MODEL"

echo "✓ Models ready on $OLLAMA_HOST"
