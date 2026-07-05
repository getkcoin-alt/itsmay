"""STT client. Provider-agnostic.

Two providers selected by `STT_PROVIDER`:

- `groq` (default in deployed mode) — posts audio to Groq's OpenAI-compatible
  `/openai/v1/audio/transcriptions` endpoint. Server-side Whisper, very fast,
  very cheap. No model download or local CPU cost.

- `local` — runs `faster-whisper` in-process. Slow first call (model download),
  fast after. Use for offline dev or when audio must not leave the host.

The public surface (`transcribe`, `health`, `loaded`) is identical across
providers; callers don't change.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import httpx

from core.config import get_settings
from core.logging import get_logger
from core.util.keypool import KeyPool, parse_retry_after

log = get_logger(__name__)


class WhisperSTT:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        compute_type: str | None = None,
        device: str | None = None,
    ) -> None:
        s = get_settings()
        self.provider = (provider or s.stt_provider).lower()
        # Local faster-whisper expects size names (small.en, large-v3-turbo, …),
        # which differ from the cloud provider's model id — so default to
        # `whisper_model` for local and `stt_model` for groq.
        default_model = s.whisper_model if self.provider == "local" else s.stt_model
        self.model_size = model or default_model
        self.base_url = (base_url or s.stt_base_url).rstrip("/")
        self.keys = KeyPool.from_csv(
            api_key if api_key is not None else s.stt_api_key, label="stt"
        )
        self.compute_type = compute_type or s.whisper_compute_type
        self.device = device or s.whisper_device

        self._local_model: Any | None = None
        self._load_lock = asyncio.Lock()
        # Groq Whisper usually returns in <3s; cap at 45s so transient hangs
        # fail-fast instead of being eaten by Railway's 300s edge timeout.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── transcribe ──────────────────────────────────────────────
    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        suffix: str = ".wav",
        language: str | None = None,
    ) -> dict:
        if self.provider == "groq":
            return await self._transcribe_groq(audio_bytes, suffix, language)
        if self.provider == "local":
            return await self._transcribe_local(audio_bytes, suffix, language)
        raise ValueError(f"unknown STT_PROVIDER: {self.provider!r}")

    async def _transcribe_groq(
        self, audio_bytes: bytes, suffix: str, language: str | None
    ) -> dict:
        url = f"{self.base_url}/audio/transcriptions"
        data: dict[str, str] = {"model": self.model_size, "response_format": "verbose_json"}
        if language:
            data["language"] = language

        attempts = max(self.keys.size, 1)
        last: httpx.Response | None = None
        for _ in range(attempts):
            lease = self.keys.lease()
            key = lease.key if lease else None
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            # files dict must be rebuilt per attempt — httpx consumes the bytes.
            files = {"file": (f"audio{suffix}", audio_bytes, _mime_for(suffix))}
            r = await self._http.post(url, files=files, data=data, headers=headers)
            if r.status_code == 429:
                self.keys.mark_rate_limited(
                    parse_retry_after(r.headers.get("retry-after")), lease=lease
                )
                last = r
                continue
            if r.status_code == 401 and self.keys.size > 1:
                self.keys.mark_invalid(lease=lease)
                last = r
                continue
            self.keys.update_from_headers(r.headers, lease=lease)
            if r.status_code >= 400:
                log.error("stt.groq.error", status=r.status_code, body=r.text[:400])
                r.raise_for_status()
            last = r
            break
        else:
            assert last is not None
            log.error("stt.groq.exhausted", status=last.status_code, body=last.text[:400])
            last.raise_for_status()

        assert last is not None
        body = last.json()
        # Groq verbose_json returns {text, language, duration, segments:[{start,end,text}]}
        segments = body.get("segments") or []
        return {
            "text": (body.get("text") or "").strip(),
            "language": body.get("language", "en"),
            "language_probability": 1.0,
            "duration": float(body.get("duration", 0.0)),
            "segments": [
                {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
                for s in segments
            ],
        }

    async def _transcribe_local(
        self, audio_bytes: bytes, suffix: str, language: str | None
    ) -> dict:
        await self._ensure_local_loaded()
        return await asyncio.to_thread(self._transcribe_local_sync, audio_bytes, suffix, language)

    async def _ensure_local_loaded(self) -> Any:
        if self._local_model is not None:
            return self._local_model
        async with self._load_lock:
            if self._local_model is None:
                log.info("whisper.local.loading", model=self.model_size, compute=self.compute_type)
                self._local_model = await asyncio.to_thread(self._load_local_sync)
                log.info("whisper.local.loaded")
            return self._local_model

    def _load_local_sync(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed. Install the local-STT extra: "
                'pip install -e ".[whisper_local]"'
            ) from e
        return WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)

    def _transcribe_local_sync(
        self, audio_bytes: bytes, suffix: str, language: str | None
    ) -> dict:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            path = Path(f.name)
        try:
            assert self._local_model is not None
            segments, info = self._local_model.transcribe(
                str(path),
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                language=language,
            )
            seg_list = list(segments)
            text = " ".join(s.text.strip() for s in seg_list).strip()
            return {
                "text": text,
                "language": info.language,
                "language_probability": float(info.language_probability),
                "duration": float(info.duration),
                "segments": [
                    {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
                    for s in seg_list
                ],
            }
        finally:
            path.unlink(missing_ok=True)

    # ── health ──────────────────────────────────────────────────
    @property
    def loaded(self) -> bool:
        if self.provider == "groq":
            return True  # remote, always "loaded"
        return self._local_model is not None

    async def health(self) -> dict:
        base = {
            "provider": self.provider,
            "model": self.model_size,
            "loaded": self.loaded,
        }
        if self.provider == "groq":
            base["base_url"] = self.base_url
            base["configured"] = self.keys.configured
            base["key_pool"] = self.keys.status()
        else:
            base["compute_type"] = self.compute_type
            base["device"] = self.device
        return base


def _mime_for(suffix: str) -> str:
    s = suffix.lower().lstrip(".")
    return {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "ogg": "audio/ogg",
        "webm": "audio/webm",
        "flac": "audio/flac",
    }.get(s, "application/octet-stream")
