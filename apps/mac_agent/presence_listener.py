"""Proactive voice output client for Scrappy Continuous Presence.

This process does one thing: long-poll the protected presence outbox and speak
queued utterances on the Mac through the existing Vault TTS endpoint + `afplay`.
It does not record the microphone, execute tools, or make consequential changes.

Run alongside the API/worker:
    python -m apps.mac_agent.presence_listener
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.environ.get("VAULT_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("VAULT_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


async def _next_utterance(client: httpx.AsyncClient) -> dict | None:
    response = await client.get(f"{API_BASE}/v1/presence/outbox/next", timeout=35)
    if response.status_code == 204:
        return None
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else None


async def _synth(client: httpx.AsyncClient, text: str, path: Path) -> None:
    async with client.stream(
        "POST",
        f"{API_BASE}/v1/voice/speak",
        json={"text": text},
        timeout=60,
    ) as response:
        response.raise_for_status()
        with path.open("wb") as fh:
            async for chunk in response.aiter_bytes():
                fh.write(chunk)


async def _play(path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "afplay",
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def main() -> None:
    print(f"Scrappy proactive presence listener → {API_BASE}")
    print("Waiting for verified SPEAK events. Ctrl+C to stop.")

    backoff = 1.0
    async with httpx.AsyncClient(headers=_headers()) as client:
        while True:
            try:
                item = await _next_utterance(client)
                backoff = 1.0
                if not item:
                    continue

                text = str(item.get("text", "")).strip()
                if not text:
                    continue

                print(f"\nscrappy: {text}", flush=True)
                tmp = Path(tempfile.mkstemp(suffix=".mp3")[1])
                try:
                    await _synth(client, text, tmp)
                    await _play(tmp)
                finally:
                    tmp.unlink(missing_ok=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[presence listener] {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
