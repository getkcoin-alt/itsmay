from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Server ────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # ── Auth (Mac ↔ Railway public URL) ──────────────────────────
    # Empty string disables auth (local dev only). Generate with `openssl rand -hex 32`.
    vault_api_key: str = ""

    # ── Postgres ──────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "vault_zeta"
    postgres_user: str = "vault"
    postgres_password: str = "change-me"
    # Railway sets DATABASE_URL on the service; if present, it overrides the parts above.
    database_url: str | None = None

    # ── Memory backend ────────────────────────────────────────────
    # "auto" → Postgres+pgvector when DATABASE_URL is set (the Railway/cloud
    # path), else a local SQLite file. SQLite is the sovereign default for
    # `scrappy` running entirely on your machine: one file, no server, no Docker,
    # brute-force cosine search (fine at single-operator scale). Force with
    # "postgres" or "sqlite".
    memory_backend: str = "auto"
    sqlite_path: str = "~/.itsmay/memory.db"

    # ── LLM (chat) ────────────────────────────────────────────────
    # provider: "openai" (any OpenAI-compatible endpoint — Groq, OpenRouter, …) or "ollama"
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    # Sub-agents (terminal agents) run on a cheaper, faster model with its OWN
    # daily-token budget, so their many iterations don't drain the main model's
    # quota. Groq's 8b-instant is plenty for running bash and reading output;
    # heavy coding is delegated to Claude Code, not this model.
    llm_agent_model: str = "llama-3.1-8b-instant"
    # Route tool-using experts (memory/email/researcher) to `llm_agent_model` too:
    # their work is mechanical tool-calling that the cheap 8b handles fine, so we
    # don't spend 70b tokens on it. Reasoning-critical experts opt out via
    # `SubAgentSpec.heavy=True` (the Strategist) and stay on the big model. Flip
    # off to force every expert back onto `llm_model`.
    experts_use_agent_model: bool = True
    # Max passes through the agentic tool loop per turn (tool call → result →
    # re-ask). Caps how many times Scrappy can chain tools/experts in one reply.
    tool_loop_max_iters: int = 4

    # ── Embeddings ────────────────────────────────────────────────
    # Default: local fastembed (ONNX, ~120MB, no API key, no rate limit).
    # Switch provider to "openai" to use any OpenAI-compatible /v1/embeddings.
    embed_provider: str = "local"
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    retrieval_k: int = 8
    recent_message_window: int = 12

    # ── STT ───────────────────────────────────────────────────────
    # provider: "groq" (OpenAI-compatible /audio/transcriptions) or "local" (faster-whisper)
    stt_provider: str = "groq"
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_api_key: str = ""
    stt_model: str = "whisper-large-v3-turbo"

    # ── TTS (ElevenLabs) ──────────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_flash_v2_5"
    elevenlabs_output_format: str = "mp3_44100_128"

    # ── Identity / mission ────────────────────────────────────────
    user_handle: str = "karnveer"
    mission_target_date: date = date(2026, 11, 23)
    mission_statement: str = "Achieve financial freedom"

    # ── Gmail connector ───────────────────────────────────────────
    # JSON blob from scripts/gmail_auth.py. If unset, the Gmail connector
    # and Email expert are silently absent from the toolbox.
    google_credentials_json: str = ""

    # ── Mac agent ─────────────────────────────────────────────────
    vault_api_base: str = "http://127.0.0.1:8000"

    # ── VAD (voice activity detection) ────────────────────────────
    vad_aggressiveness: int = 2   # 0–3, higher = less sensitive to noise
    vad_silence_ms: int = 700     # silence required to end utterance

    # ── Optional: local Ollama (dev only) ─────────────────────────
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma3:latest"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_keep_alive: str = "30m"

    # ── Optional: local Whisper (dev only) ────────────────────────
    whisper_model: str = "small.en"  # used when stt_provider=local
    whisper_compute_type: str = "int8"
    whisper_device: str = "auto"

    @property
    def pg_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
