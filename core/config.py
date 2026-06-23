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

    # ── LLM (chat) ────────────────────────────────────────────────
    # provider: "openai" (any OpenAI-compatible endpoint — Groq, OpenRouter, …) or "ollama"
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"

    # ── Embeddings ────────────────────────────────────────────────
    # Default: Voyage AI free tier — OpenAI-compatible /embeddings endpoint.
    embed_provider: str = "openai"
    embed_base_url: str = "https://api.voyageai.com/v1"
    embed_api_key: str = ""
    embed_model: str = "voyage-3-lite"
    embed_dim: int = 512
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
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    elevenlabs_output_format: str = "mp3_44100_128"

    # ── Identity / mission ────────────────────────────────────────
    user_handle: str = "karnveer"
    mission_target_date: date = date(2026, 11, 23)
    mission_statement: str = "Achieve financial freedom"

    # ── Mac agent ─────────────────────────────────────────────────
    vault_api_base: str = "http://127.0.0.1:8000"

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
