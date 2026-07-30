"""Vault Zeta FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
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
from core.util.redis_pool import close_redis
from core.voice.stt_whisper import WhisperSTT
from core.voice.tts_elevenlabs import ElevenLabsTTS


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


def _inject_site_verification(html: str, token: str) -> str:
    """Insert the Google Search Console `<meta>` verification tag into <head>.

    No-op when token is empty. Env-driven so the token never has to be committed
    into the static HTML.
    """
    token = (token or "").strip()
    if not token:
        return html
    safe = token.replace('"', "&quot;").replace("<", "").replace(">", "")
    tag = f'  <meta name="google-site-verification" content="{safe}" />\n'
    if "</head>" in html:
        return html.replace("</head>", tag + "</head>", 1)
    return tag + html


def _render_index(html: str, *, base_url: str, gsc_token: str = "") -> str:
    """Weave host-specific tags into the console shell: the Google verification
    meta (when configured) and absolute social-preview URLs — some crawlers reject
    a relative og:image and expect an og:url."""
    base = base_url.rstrip("/")
    html = _inject_site_verification(html, gsc_token)
    html = html.replace('content="/static/og-image.png"', f'content="{base}/static/og-image.png"')
    if "</head>" in html:
        html = html.replace(
            "</head>", f'  <meta property="og:url" content="{base}/" />\n</head>', 1
        )
    return html


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Vault Zeta Node SSN-92C",
        version="0.1.0",
        description="Scrappy Singh — sovereign personal AI operator",
        lifespan=lifespan,
    )

    # Bearer-token guard. The console UI shell (/, /static, /status) stays open
    # so the page can load and prompt for the key; the health routes (/v1/live,
    # /v1/health) are open for the platform + monitoring; every /v1 data endpoint
    # stays protected.
    app.add_middleware(
        BearerAuthMiddleware,
        token=settings.vault_api_key,
        allow_paths=(
            "/", "/status", "/v1/live", "/v1/health", "/favicon.ico",
            "/robots.txt", "/sitemap.xml",
        ),
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
    index_html = static_dir / "index.html"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def console(request: Request) -> HTMLResponse:
        # Serve the console shell with host-specific tags (Search Console
        # verification + absolute social-preview URLs) woven into <head>.
        html = _render_index(
            index_html.read_text(encoding="utf-8"),
            base_url=str(request.base_url),
            gsc_token=settings.google_site_verification,
        )
        return HTMLResponse(html)

    @app.get("/robots.txt", include_in_schema=False)
    async def robots(request: Request) -> PlainTextResponse:
        sitemap_url = str(request.base_url).rstrip("/") + "/sitemap.xml"
        body = (
            "User-agent: *\n"
            "Disallow: /v1/\n"  # keep crawlers out of the API surface
            "Allow: /\n"
            f"Sitemap: {sitemap_url}\n"
        )
        return PlainTextResponse(body)

    @app.get("/sitemap.xml", include_in_schema=False)
    async def sitemap(request: Request) -> Response:
        root = str(request.base_url).rstrip("/") + "/"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{root}</loc></url>\n"
            "</urlset>\n"
        )
        return Response(content=xml, media_type="application/xml")

    @app.get("/v1/live", tags=["meta"])
    async def live() -> dict:
        # Liveness only: the process is up and the event loop is serving. Makes NO
        # upstream calls (no LLM / embedder / DB), so a rate-limited or cooling-down
        # key can't fail the platform healthcheck and trigger a restart loop (#19).
        # /v1/health stays the DEEP readiness check for humans + monitoring.
        return {"status": "ok"}

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
            "copyright": (
                "© 2026 Karnveer Singh — Designed & Developed by "
                "Karnveer Singh (www.karnveer.com)"
            ),
        }

    return app


app = create_app()
