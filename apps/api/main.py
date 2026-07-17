"""Vault Zeta FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.api.middleware.auth import BearerAuthMiddleware
from apps.api.routers import agents as agents_router
from apps.api.routers import browser as browser_router
from apps.api.routers import chat as chat_router
from apps.api.routers import console as console_router
from apps.api.routers import identity as identity_router
from apps.api.routers import memory as memory_router
from apps.api.routers import voice as voice_router
from apps.api.routers import worker as worker_router
from core.brain.llm import LLMClient
from core.config import get_settings
from core.logging import configure_logging, get_logger
from core.memory.backend import resolve_backend
from core.memory.db import close_pool, get_pool
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.migrate import run_migrations
from core.memory.semantic import SemanticStore
from core.memory.sqlite_store import SqliteEpisodicStore, SqliteSemanticStore, ensure_schema
from core.voice.stt_whisper import WhisperSTT
from core.voice.tts_elevenlabs import ElevenLabsTTS


from core.util.redis_pool import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("api")

    backend = resolve_backend()
    app.state.memory_backend = backend
    log.info("api.startup", memory_backend=backend)

    if backend == "sqlite":
        # Sovereign local path: a single file, no server, no migrations.
        path = ensure_schema(get_settings().sqlite_path)
        app.state.episodic = SqliteEpisodicStore(path)
        app.state.semantic = SqliteSemanticStore(path)
    else:
        pool = await get_pool()
        await run_migrations(pool)
        app.state.episodic = EpisodicStore()
        app.state.semantic = SemanticStore()

    app.state.llm = LLMClient()
    app.state.embedder = Embedder()
    app.state.tts = ElevenLabsTTS()
    app.state.stt = WhisperSTT()

    yield

    log.info("api.shutdown")
    await app.state.llm.aclose()
    await app.state.embedder.aclose()
    await app.state.tts.aclose()
    await app.state.stt.aclose()
    close_redis()
    if backend == "postgres":
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Vault Zeta Node SSN-92C",
        version="0.1.0",
        description="Scrappy Singh — sovereign personal AI operator",
        lifespan=lifespan,
    )

    # Bearer-token guard. The console UI shell (/, /static, /status) stays open
    # so the page can load and prompt for the key; every /v1 data endpoint
    # except /v1/health stays protected.
    app.add_middleware(
        BearerAuthMiddleware,
        token=settings.vault_api_key,
        allow_paths=("/", "/status", "/v1/health", "/favicon.ico"),
        allow_prefixes=("/static/",),
    )

    app.include_router(chat_router.router)
    app.include_router(voice_router.router)
    app.include_router(console_router.router)
    app.include_router(identity_router.router)
    app.include_router(browser_router.router)
    app.include_router(agents_router.router)
    app.include_router(memory_router.router)
    app.include_router(worker_router.router)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/status", tags=["meta"])
    async def status() -> dict:
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
