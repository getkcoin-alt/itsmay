"""self.request_secret — Scrappy asks for a third-party key; the value never touches him.

When a self-improvement needs a service credential, Scrappy names it and says
why. The OPERATOR enters the value client-side (`scrappy secret <name>`), which
POSTs it to the server; it's written to ~/.itsmay/config.env and the settings
cache is cleared (hot reload). Scrappy only ever learns that "<name> is now set"
— the value never passes through the model, the transcript, or memory.

Only whitelisted third-party credential settings can be set this way — never
security-critical config (the auth key, DB URL, the self-modify switch, Redis /
Postgres). This is the secrets analog of the self-modification guard: default-deny.
"""

from __future__ import annotations

from pathlib import Path

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

# Third-party credential settings Scrappy may request. Each maps to a real
# Settings field (so it hot-reloads and is usable). Security-critical config is
# deliberately absent — the whitelist blocks everything unlisted.
ALLOWED_SECRETS: dict[str, str] = {
    "llm_api_key": "chat/LLM API key (Groq or any OpenAI-compatible endpoint)",
    "elevenlabs_api_key": "ElevenLabs API key (expressive cloud voice)",
    "stt_api_key": "speech-to-text API key",
    "embed_api_key": "embeddings API key",
    "google_credentials_json": "Google OAuth credentials JSON (Gmail / Calendar)",
}


def is_allowed(name: str) -> bool:
    return (name or "").strip().lower() in ALLOWED_SECRETS


def _config_path() -> Path:
    return Path.home() / ".itsmay" / "config.env"


def set_secret(name: str, value: str, *, config_path: Path | None = None) -> str:
    """Write NAME=value into config.env (create or update) and hot-reload settings.

    Returns the uppercased env key that was set. The value is NEVER logged. Raises
    ValueError for a non-allowed name or empty value.
    """
    if not is_allowed(name):
        raise ValueError(f"{name!r} is not a settable secret")
    if not value:
        raise ValueError("empty value")

    path = config_path or _config_path()
    key = name.strip().upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []

    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n")
    try:
        path.chmod(0o600)  # secrets file: owner read/write only
    except OSError:
        pass
    get_settings.cache_clear()  # next get_settings() re-reads config.env
    log.info("secret.set", name=key)  # name only — never the value
    return key


def secret_status(name: str) -> bool:
    """Is this secret currently set (non-empty)? Never reveals the value."""
    return bool(getattr(get_settings(), name.strip().lower(), None))


def secrets_overview() -> list[dict]:
    """Which requestable secrets are set — names + descriptions, no values."""
    return [
        {"name": k.upper(), "description": desc, "set": secret_status(k)}
        for k, desc in sorted(ALLOWED_SECRETS.items())
    ]
