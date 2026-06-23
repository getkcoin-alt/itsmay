"""Vault Zeta FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.middleware.auth import BearerAuthMiddleware
from apps.api.routers import chat as chat_router
from apps.api.routers import voice as voice_router
from core.brain.llm import LLMClient
from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.memory.db import close_pool, get_pool
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.migrate import run_migrations
from core.memory.semantic import SemanticStore
from core.voice.stt_whisper import WhisperSTT
from core.voice.tts_elevenlabs import ElevenLabsTTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("api")
    log.info("api.startup")

    pool = await get_pool()
    await run_migrations(pool)

    app.state.llm = LLMClient()
    app.state.embedder = Embedder()
    app.state.episodic = EpisodicStore()
    app.state.semantic = SemanticStore()
    app.state.tts = ElevenLabsTTS()
    app.state.stt = WhisperSTT()

    yield

    log.info("api.shutdown")
    await app.state.llm.aclose()
    await app.state.embedder.aclose()
    await app.state.tts.aclose()
    await app.state.stt.aclose()
    await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Vault Zeta Node SSN-92C",
        version="0.1.0",
        description="Scrappy Singh — sovereign personal AI operator",
        lifespan=lifespan,
    )

    # Bearer-token guard. Leaves root + health open for liveness probes.
    app.add_middleware(
        BearerAuthMiddleware,
        token=settings.vault_api_key,
        allow_paths=("/", "/v1/health"),
    )

    app.include_router(chat_router.router)
    app.include_router(voice_router.router)

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "name": "Vault Zeta Node SSN-92C",
            "agent": "Scrappy Singh",
            "version": app.version,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "stt_provider": settings.stt_provider,
            "auth_enabled": bool(settings.vault_api_key),
        }

    return app


app = create_app()
