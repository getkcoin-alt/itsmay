"""Embeddings client. Local-first by default, optional remote providers.

Providers selected by `EMBED_PROVIDER`:

- `local` (default) — `fastembed` running ONNX in-process. No external API,
  no rate limits, no per-request cost. Default model is `BAAI/bge-small-en-v1.5`
  (384 dim, ~120MB quantized download on first request).
- `openai` — any OpenAI-compatible `/v1/embeddings` endpoint (OpenAI, Voyage,
  Together, Mistral). Comma-separated `EMBED_API_KEY` rotates on 429/401.
- `ollama` — Ollama's `/api/embeddings`. Local-only dev with Ollama.

The public surface (`embed`, `health`) is identical across providers.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
import numpy as np

from core.config import get_settings
from core.logging import get_logger
from core.util.keypool import KeyPool, parse_retry_after

log = get_logger(__name__)


class Embedder:
    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        self.provider = (provider or s.embed_provider).lower()
        self.base_url = (base_url or s.embed_base_url).rstrip("/")
        self.model = model or s.embed_model
        self.dim = s.embed_dim
        self.keys = KeyPool.from_csv(
            api_key if api_key is not None else s.embed_api_key, label="embed"
        )
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        self._local_model: Any | None = None
        self._local_lock = threading.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> np.ndarray:
        if self.provider == "local":
            vec = await self._embed_local(text)
        elif self.provider == "openai":
            vec = await self._embed_openai(text)
        elif self.provider == "ollama":
            vec = await self._embed_ollama(text)
        else:
            raise ValueError(f"unknown EMBED_PROVIDER: {self.provider!r}")
        arr = np.asarray(vec, dtype=np.float32)
        if arr.shape[0] != self.dim:
            log.warning(
                "embed.dim_mismatch",
                model=self.model,
                returned=int(arr.shape[0]),
                expected=self.dim,
            )
        return arr

    # ── local fastembed ──────────────────────────────────────────
    def _ensure_local_loaded_sync(self) -> Any:
        # `fastembed` import is heavy (loads onnxruntime); defer to first call.
        with self._local_lock:
            if self._local_model is None:
                from fastembed import TextEmbedding

                log.info("embed.local.loading", model=self.model)
                self._local_model = TextEmbedding(model_name=self.model)
                log.info("embed.local.loaded", model=self.model)
        return self._local_model

    def _embed_local_sync(self, text: str) -> list[float]:
        model = self._ensure_local_loaded_sync()
        # `.embed(...)` returns a generator of numpy arrays
        vecs = list(model.embed([text]))
        return vecs[0].tolist()

    async def _embed_local(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_local_sync, text)

    # ── OpenAI-compatible ────────────────────────────────────────
    async def _embed_openai(self, text: str) -> list[float]:
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": text}
        attempts = max(self.keys.size, 1)
        last: httpx.Response | None = None
        for _ in range(attempts):
            key = self.keys.current()
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            r = await self._client.post(url, json=payload, headers=headers)
            if r.status_code == 429:
                self.keys.mark_rate_limited(parse_retry_after(r.headers.get("retry-after")))
                last = r
                continue
            if r.status_code == 401 and self.keys.size > 1:
                self.keys.mark_invalid()
                last = r
                continue
            self.keys.update_from_headers(r.headers)
            if r.status_code >= 400:
                log.error("embed.openai.error", status=r.status_code, body=r.text[:400])
                r.raise_for_status()
            return r.json()["data"][0]["embedding"]
        assert last is not None
        log.error("embed.openai.exhausted", status=last.status_code, body=last.text[:400])
        last.raise_for_status()
        return []  # unreachable

    # ── Ollama ───────────────────────────────────────────────────
    async def _embed_ollama(self, text: str) -> list[float]:
        url = f"{self.base_url}/api/embeddings"
        r = await self._client.post(url, json={"model": self.model, "prompt": text})
        r.raise_for_status()
        return r.json()["embedding"]

    # ── health ───────────────────────────────────────────────────
    async def health(self) -> dict:
        try:
            v = await self.embed("vault zeta embedder health probe")
            return {
                "ok": True,
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url if self.provider != "local" else None,
                "dim": int(v.shape[0]),
                "dim_matches_config": int(v.shape[0]) == self.dim,
                "key_pool": self.keys.status() if self.provider == "openai" else None,
            }
        except Exception as e:
            return {
                "ok": False,
                "provider": self.provider,
                "model": self.model,
                "error": str(e),
            }
