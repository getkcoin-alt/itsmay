"""Memory backend selection — Postgres (cloud) vs local SQLite (sovereign).

One seam that both the API startup and `scrappy status` use to agree on which
store is live. The Postgres stores and the SQLite stores expose the same method
surface, so the rest of the app is backend-agnostic — it just reads whichever
pair `make_stores()` hands back off `app.state`.
"""

from __future__ import annotations

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)


def resolve_backend() -> str:
    """Return "postgres" or "sqlite" for the current config.

    "auto" picks Postgres when a DATABASE_URL is configured (Railway/cloud),
    otherwise the local SQLite file — so a fresh local install needs no server.
    """
    s = get_settings()
    choice = (s.memory_backend or "auto").strip().lower()
    if choice in ("postgres", "sqlite"):
        return choice
    return "postgres" if s.database_url else "sqlite"


def describe_backend() -> dict:
    """Human-facing summary for health/status output."""
    backend = resolve_backend()
    s = get_settings()
    if backend == "sqlite":
        from pathlib import Path

        return {"backend": "sqlite", "location": str(Path(s.sqlite_path).expanduser())}
    # Don't leak credentials — just the host/db part of the DSN.
    return {"backend": "postgres", "location": s.pg_dsn.split("@")[-1]}
