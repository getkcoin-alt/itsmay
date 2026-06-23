"""LLM client. Streaming-first, provider-agnostic, with rotating API keys.

Two providers selected by `LLM_PROVIDER`:

- `openai` (default) — any OpenAI-compatible chat-completions endpoint
  (Groq, OpenRouter, Together AI, vLLM, OpenAI). Streaming via SSE.
- `ollama` — native `/api/chat` for offline local-dev.

`LLM_API_KEY` may be a single key OR a comma-separated list. On 429 (rate
limited) or 401 (invalid) the client cools the current key down and rotates to
the next; retries the initial request up to once per pooled key before giving
up. Proactive rotation kicks in when the provider's `x-ratelimit-remaining-*`
headers fall below configured floors.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.util.keypool import KeyPool, parse_retry_after

log = get_logger(__name__)

Role = Literal["system", "user", "assistant", "tool"]
Provider = Literal["openai", "ollama"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatChunk:
    delta: str
    done: bool
    eval_count: int | None = None
    prompt_eval_count: int | None = None
    # Populated on the final (done=True) chunk if the model emitted tool calls.
    # Each item: {"id": str, "name": str, "arguments": dict}
    tool_calls: list[dict] | None = None


class LLMClient:
    def __init__(
        self,
        provider: Provider | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        self.provider: Provider = (provider or s.llm_provider).lower()  # type: ignore[assignment]
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.model = model or s.llm_model
        self.keep_alive = s.ollama_keep_alive
        self.keys = KeyPool.from_csv(api_key if api_key is not None else s.llm_api_key, label="llm")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── public: chat streaming ──────────────────────────────────
    async def chat_stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        num_ctx: int = 8192,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ChatChunk]:
        if self.provider == "openai":
            async for chunk in self._chat_stream_openai(
                messages, temperature, tools=tools, tool_choice=tool_choice
            ):
                yield chunk
        elif self.provider == "ollama":
            async for chunk in self._chat_stream_ollama(messages, temperature, num_ctx):
                yield chunk
        else:
            raise ValueError(f"unknown LLM_PROVIDER: {self.provider!r}")

    # ── OpenAI-compatible ───────────────────────────────────────
    async def _open_openai_stream(self, payload: dict) -> httpx.Response:
        """Open a streaming request, rotating keys on 429/401. Returns the
        response with status checked. Caller owns aclose()."""
        url = f"{self.base_url}/chat/completions"
        attempts = max(self.keys.size, 1)
        last_resp: httpx.Response | None = None
        for _ in range(attempts):
            key = self.keys.current()
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = self._client.build_request("POST", url, json=payload, headers=headers)
            resp = await self._client.send(req, stream=True)
            if resp.status_code == 429:
                self.keys.mark_rate_limited(parse_retry_after(resp.headers.get("retry-after")))
                await resp.aclose()
                last_resp = resp
                continue
            if resp.status_code == 401 and self.keys.size > 1:
                self.keys.mark_invalid()
                await resp.aclose()
                last_resp = resp
                continue
            self.keys.update_from_headers(resp.headers)
            return resp
        # Exhausted retries — surface last bad response.
        assert last_resp is not None
        return last_resp

    async def _chat_stream_openai(
        self,
        messages: list[Message],
        temperature: float,
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> AsyncIterator[ChatChunk]:
        payload: dict = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        resp = await self._open_openai_stream(payload)
        try:
            if resp.status_code >= 400:
                body = await resp.aread()
                log.error(
                    "llm.openai.error",
                    status=resp.status_code,
                    body=body[:400],
                    model=self.model,
                    pool=self.keys.status(),
                )
                resp.raise_for_status()

            prompt_tokens: int | None = None
            completion_tokens: int | None = None
            # Streaming tool_call accumulators, keyed by `index`.
            tc_state: dict[int, dict] = {}

            def _finalize_tool_calls() -> list[dict] | None:
                if not tc_state:
                    return None
                out: list[dict] = []
                for _idx, st in sorted(tc_state.items()):
                    args_str = st.get("arguments_str", "")
                    try:
                        args_obj = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args_obj = {"_raw": args_str}
                    out.append(
                        {
                            "id": st.get("id") or f"call_{_idx}",
                            "name": st.get("name") or "",
                            "arguments": args_obj,
                        }
                    )
                return out

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    yield ChatChunk(
                        delta="",
                        done=True,
                        prompt_eval_count=prompt_tokens,
                        eval_count=completion_tokens,
                        tool_calls=_finalize_tool_calls(),
                    )
                    return
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    log.warning("llm.openai.bad_json", line=data_str[:200])
                    continue
                usage = obj.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta_obj = choice.get("delta") or {}
                delta = delta_obj.get("content") or ""

                # Accumulate streamed tool_calls per OpenAI tool-call delta format.
                for tc in delta_obj.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    st = tc_state.setdefault(
                        idx, {"id": None, "name": None, "arguments_str": ""}
                    )
                    if tc.get("id"):
                        st["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        st["name"] = fn["name"]
                    if fn.get("arguments"):
                        st["arguments_str"] += fn["arguments"]

                finish = choice.get("finish_reason")
                if delta or finish:
                    yield ChatChunk(
                        delta=delta,
                        done=bool(finish),
                        prompt_eval_count=prompt_tokens,
                        eval_count=completion_tokens,
                        tool_calls=_finalize_tool_calls() if finish else None,
                    )
                    if finish:
                        return
        finally:
            await resp.aclose()

    # ── Ollama ──────────────────────────────────────────────────
    async def _chat_stream_ollama(
        self, messages: list[Message], temperature: float, num_ctx: int
    ) -> AsyncIterator[ChatChunk]:
        payload = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        url = f"{self.base_url}/api/chat"
        async with self._client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("llm.ollama.bad_json", line=line[:200])
                    continue
                delta = obj.get("message", {}).get("content", "")
                done = bool(obj.get("done", False))
                yield ChatChunk(
                    delta=delta,
                    done=done,
                    eval_count=obj.get("eval_count"),
                    prompt_eval_count=obj.get("prompt_eval_count"),
                )
                if done:
                    return

    # ── health ──────────────────────────────────────────────────
    async def health(self) -> dict:
        if self.provider == "openai":
            return await self._health_openai()
        return await self._health_ollama()

    async def _health_openai(self) -> dict:
        key = self.keys.current()
        if not key:
            return {
                "ok": False,
                "provider": "openai-compatible",
                "base_url": self.base_url,
                "error": "no api key configured",
            }
        try:
            url = f"{self.base_url}/models"
            r = await self._client.get(
                url, headers={"Authorization": f"Bearer {key}"}, timeout=5.0
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            ids = [m.get("id") for m in data]
            return {
                "ok": True,
                "provider": "openai-compatible",
                "base_url": self.base_url,
                "model": self.model,
                "chat_model_loaded": self.model in ids,
                "models_available": len(ids),
                "key_pool": self.keys.status(),
            }
        except Exception as e:
            return {
                "ok": False,
                "provider": "openai-compatible",
                "base_url": self.base_url,
                "error": str(e),
                "key_pool": self.keys.status(),
            }

    async def _health_ollama(self) -> dict:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]

            def _present(target: str) -> bool:
                if target in models:
                    return True
                if ":" not in target:
                    return f"{target}:latest" in models
                return False

            return {
                "ok": True,
                "provider": "ollama",
                "base_url": self.base_url,
                "models": models,
                "chat_model_loaded": _present(self.model),
            }
        except Exception as e:
            return {"ok": False, "provider": "ollama", "error": str(e)}


# Back-compat: old code imports OllamaClient.
OllamaClient = LLMClient
