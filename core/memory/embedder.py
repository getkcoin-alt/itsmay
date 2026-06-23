"""Embeddings client. Provider-agnostic, with rotating API keys.

Providers selected by `EMBED_PROVIDER`:

- `openai` (default) — any OpenAI-compatible `/v1/embeddings` endpoint
  (OpenAI, Together AI, Voyage via compat layer, etc.). Default model is
  OpenAI `text-embedding-3-small` (1536 dim).
- `ollama` — Ollama's `/api/embeddings`. Local-only dev.

`EMBED_API_KEY` can be a single key OR comma-separated. Rotation behavior
matches `core/util/keypool.KeyPool`.
"""

from __future__ import annotations

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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> np.ndarray:
        if self.provider == "openai":
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

    async def _embed_ollama(self, text: str) -> list[float]:
        url = f"{self.base_url}/api/embeddings"
        r = await self._client.post(url, json={"model": self.model, "prompt": text})
        r.raise_for_status()
        return r.json()["embedding"]

    async def health(self) -> dict:
        try:
            v = await self.embed("vault zeta embedder health probe")
            return {
                "ok": True,
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url,
                "dim": int(v.shape[0]),
                "dim_matches_config": int(v.shape[0]) == self.dim,
                "key_pool": self.keys.status(),
            }
        except Exception as e:
            return {
                "ok": False,
                "provider": self.provider,
                "model": self.model,
                "error": str(e),
                "key_pool": self.keys.status(),
            }
