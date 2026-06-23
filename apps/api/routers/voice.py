"""Voice endpoints.

POST /v1/voice/speak       — text in, streamed audio out (ElevenLabs TTS)
POST /v1/voice/transcribe  — audio in, JSON transcript out (Whisper STT)
GET  /v1/voice/health      — config + load state of both subsystems
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.voice.stt_whisper import WhisperSTT
from core.voice.tts_elevenlabs import ElevenLabsTTS

router = APIRouter(prefix="/v1/voice", tags=["voice"])


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice_id: str | None = None
    model_id: str | None = None


def get_tts(req: Request) -> ElevenLabsTTS:
    return req.app.state.tts


def get_stt(req: Request) -> WhisperSTT:
    return req.app.state.stt


@router.post("/speak")
async def speak(body: SpeakRequest, tts: ElevenLabsTTS = Depends(get_tts)) -> StreamingResponse:
    if not tts.configured:
        raise HTTPException(503, "ElevenLabs not configured (set ELEVENLABS_API_KEY)")

    # Advance the generator to its first chunk so upstream errors (401/403/etc.) map
    # to a clean HTTP error response instead of a torn streaming body.
    gen = tts.stream(body.text, voice_id=body.voice_id, model_id=body.model_id)
    try:
        first_chunk = await gen.__anext__()
    except httpx.HTTPStatusError as e:
        detail = (e.response.text or "")[:500] if e.response is not None else str(e)
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status, f"ElevenLabs error: {detail}") from e
    except StopAsyncIteration as e:
        raise HTTPException(502, "ElevenLabs returned no audio") from e

    async def with_first():
        yield first_chunk
        async for chunk in gen:
            yield chunk

    return StreamingResponse(
        with_first(),
        media_type="audio/mpeg",
        headers={"X-Voice-Id": body.voice_id or tts.voice_id},
    )


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: str | None = None,
    stt: WhisperSTT = Depends(get_stt),
) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio payload")
    suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
    try:
        return await stt.transcribe(data, suffix=suffix, language=language)
    except Exception as e:
        raise HTTPException(500, f"transcription failed: {e}") from e


@router.get("/health")
async def voice_health(
    tts: ElevenLabsTTS = Depends(get_tts),
    stt: WhisperSTT = Depends(get_stt),
) -> dict:
    return {
        "tts": {
            "provider": "elevenlabs",
            "configured": tts.configured,
            "voice_id": tts.voice_id,
            "model": tts.model_id,
        },
        "stt": {
            "provider": "faster-whisper",
            "model": stt.model_size,
            "compute_type": stt.compute_type,
            "device": stt.device,
            "loaded": stt.loaded,
        },
    }
