"""ElevenLabs streaming TTS client.

Single surface so swapping TTS providers (Cartesia, OpenAI, Resemble) only touches this file.
Defaults to MP3 output for browser-friendly playback. For lowest latency, request PCM and
pipe directly to the audio device.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx
import websockets

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


def _sample_rate_from_format(fmt: str) -> int:
    """'pcm_24000' → 24000, 'mp3_44100_128' → 44100. Default 44100."""
    for part in fmt.split("_")[1:]:
        if part.isdigit() and len(part) >= 4:
            return int(part)
    return 44100


class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io/v1"

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str | None = None,
        output_format: str | None = None,
    ) -> None:
        s = get_settings()
        self.api_key = api_key or s.elevenlabs_api_key
        self.voice_id = voice_id or s.elevenlabs_voice_id
        # eleven_turbo_v2_5 supports full style/speaker-boost and has lower latency
        self.model_id = model_id or s.elevenlabs_model_id
        self.output_format = output_format or s.elevenlabs_output_format
        # Raw PCM (pcm_*) can pipe straight to the audio device; mp3_* is for the
        # web/API path. sample_rate lets a local player know the rate.
        self.sample_rate = _sample_rate_from_format(self.output_format)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model_id: str | None = None,
        optimize_streaming_latency: int = 3,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream audio bytes for `text`. Yields chunks suitable for HTTP streaming.

        `previous_text`/`next_text` give ElevenLabs the surrounding context so a
        sentence rendered in isolation keeps its intonation (no flat, restarted
        prosody at each chunk when we speak sentence-by-sentence).
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

        vid = voice_id or self.voice_id
        url = f"{self.BASE}/text-to-speech/{vid}/stream"
        params = {
            "optimize_streaming_latency": optimize_streaming_latency,
            "output_format": self.output_format,
        }
        headers = {
            "xi-api-key": self.api_key,
            "accept": "audio/pcm" if self.output_format.startswith("pcm") else "audio/mpeg",
            "content-type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": model_id or self.model_id,
            # ── Voice personality tuning ──────────────────────────────────
            # Target: cheerful, childish-robotic, clean, energetic AI companion.
            # stability   0.28 → highly expressive & emotionally varied (playful swings)
            # similarity  0.82 → locks in the voice's synthetic-clean character
            # style       0.60 → amplifies the playful/energetic style of the voice
            # speaker_boost True → crisp, present, forward sound ("in the room")
            "voice_settings": {
                "stability": 0.28,
                "similarity_boost": 0.82,
                "style": 0.60,
                "use_speaker_boost": True,
            },
        }
        if previous_text:
            payload["previous_text"] = previous_text
        if next_text:
            payload["next_text"] = next_text
        async with self._client.stream(
            "POST", url, params=params, headers=headers, json=payload
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                log.error("elevenlabs.error", status=resp.status_code, body=body[:400])
                resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    async def stream_input_ws(
        self,
        text_iterator,
        *,
        voice_id: str | None = None,
        model_id: str | None = None,
        output_format: str = "pcm_24000",
    ) -> AsyncIterator[bytes]:
        """Stream audio for a *growing* text stream over ElevenLabs' websocket.

        `stream()` renders one finished string; this one feeds sentences in as the
        model produces them, so speech starts before the reply is complete.
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")

        vid = voice_id or self.voice_id
        mid = model_id or self.model_id
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{vid}/stream-input"
            f"?model_id={mid}&output_format={output_format}"
        )

        async with websockets.connect(url) as ws:
            bos_message = {
                "text": " ",
                "voice_settings": {
                    "stability": 0.28,
                    "similarity_boost": 0.82,
                    "style": 0.60,
                    "use_speaker_boost": True,
                },
                "xi_api_key": self.api_key,
            }
            await ws.send(json.dumps(bos_message))

            async def send_text() -> None:
                try:
                    async for chunk in text_iterator:
                        if chunk:
                            await ws.send(
                                json.dumps({"text": chunk, "try_trigger_generation": True})
                            )
                    await ws.send(json.dumps({"text": ""}))
                except Exception as e:
                    log.error("elevenlabs.ws.send.error", exc_info=e)
                    # Best-effort end-of-stream so the socket closes cleanly; if the
                    # connection is already gone there is nothing left to say.
                    try:
                        await ws.send(json.dumps({"text": ""}))
                    except Exception:
                        pass

            send_task = asyncio.create_task(send_text())

            try:
                while True:
                    try:
                        response = await ws.recv()
                        data = json.loads(response)
                        if data.get("audio"):
                            chunk = base64.b64decode(data["audio"])
                            yield chunk
                        if data.get("isFinal"):
                            break
                    except websockets.exceptions.ConnectionClosed as e:
                        log.error("elevenlabs.ws.closed", code=e.code, reason=e.reason)
                        break
            finally:
                send_task.cancel()

    async def list_voices(self) -> list[dict]:
        """The account's available voices: [{id, name, labels, description, category}].
        `labels` carries ElevenLabs' tags (gender, age, accent, description, use_case)."""
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        r = await self._client.get(
            f"{self.BASE}/voices", headers={"xi-api-key": self.api_key}
        )
        r.raise_for_status()
        out: list[dict] = []
        for v in r.json().get("voices", []):
            out.append(
                {
                    "id": v.get("voice_id", ""),
                    "name": v.get("name", ""),
                    "labels": v.get("labels") or {},
                    "description": v.get("description") or "",
                    "category": v.get("category", ""),
                }
            )
        return out
