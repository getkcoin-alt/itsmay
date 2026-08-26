"""Model settings API — list/switch LLM providers and local Ollama models.

Endpoints:
  GET  /v1/settings/models          current config + available models
  PUT  /v1/settings/models          switch provider/model/key
  GET  /v1/settings/ollama/models   list locally installed Ollama models
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1/settings", tags=["settings"])


class ModelConfig(BaseModel):
    provider: str | None = None        # "openai" | "ollama"
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    agent_model: str | None = None


# ── read current config ──────────────────────────────────────────

@router.get("/models")
async def get_models(request: Request):
    s = get_settings()
    return {
        "scrappy": {
            "provider": s.llm_provider,
            "base_url": s.llm_base_url,
            "model": s.llm_model,
            "agent_model": s.llm_agent_model,
            "has_key": bool(s.llm_api_key),
        },
        "mini": {
            "provider": "ollama",
            "base_url": s.ollama_host,
            "model": s.companion_model,
            "persona": s.companion_persona,
        },
        "ollama": {
            "host": s.ollama_host,
        },
    }


# ── list local Ollama models ─────────────────────────────────────

@router.get("/ollama/models")
async def list_ollama_models():
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{s.ollama_host}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = []
            for m in data.get("models", []):
                details = m.get("details", {})
                models.append({
                    "name": m.get("name", ""),
                    "size_bytes": m.get("size", 0),
                    "size_human": _human_size(m.get("size", 0)),
                    "family": details.get("family", ""),
                    "parameter_size": details.get("parameter_size", ""),
                    "quantization": details.get("quantization_level", ""),
                })
            return {"available": True, "models": models}
    except Exception as e:
        log.warning("ollama.unreachable", error=str(e))
        return {"available": False, "models": [], "error": str(e)}


# ── update config ────────────────────────────────────────────────

@router.put("/models")
async def update_models(body: ModelConfig, request: Request):
    """Update .env with new model settings. Takes effect on next server restart."""
    env_path = Path.cwd() / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    updates: dict[str, str] = {}
    if body.provider is not None:
        updates["LLM_PROVIDER"] = body.provider
    if body.base_url is not None:
        updates["LLM_BASE_URL"] = body.base_url
    if body.api_key is not None:
        updates["LLM_API_KEY"] = body.api_key
    if body.model is not None:
        updates["LLM_MODEL"] = body.model
    if body.agent_model is not None:
        updates["LLM_AGENT_MODEL"] = body.agent_model

    # Merge into existing lines (update in place, append new).
    written = set()
    new_lines: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")
    log.info("settings.models.updated", keys=list(updates.keys()))

    return {
        "status": "saved",
        "note": "Restart the server to apply changes",
        "updated": list(updates.keys()),
    }


# ── test provider connection ─────────────────────────────────────

@router.post("/test-provider")
async def test_provider(body: ModelConfig):
    """Quick connectivity check — hit the models endpoint of the provider."""
    base = (body.base_url or "").rstrip("/")
    key = body.api_key or ""
    if not base:
        return {"ok": False, "error": "No base_url provided"}

    try:
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                model_count = len(data.get("data", data.get("models", [])))
                return {"ok": True, "status": resp.status_code, "model_count": model_count}
            else:
                return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
