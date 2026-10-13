"""Tenant registry — one unique URL + PIN per person, sharing one deployment.

Mini AI's "gift device" mode (`MINI_OWNER_NAME`/`MINI_ONLY`) assumes exactly
one owner per running server. This module lifts that assumption: a *tenant*
is a `(slug, pin, owner_name, db_path)` row — visiting `/u/<slug>` and typing
the right PIN gets you *that* tenant's own Mini, backed by *its own* SQLite
file (profiles + memory), completely isolated from every other tenant on the
same server. `core.companion.runtime.build_engine(path=...)` already accepts
a per-call path; this registry is what decides which path a given request
gets, instead of the process-wide default.

PINs are short (4 digits, easy to type on a phone) and checked with a
constant-time hash comparison, same spirit as `apps/api/middleware/auth.py`'s
bearer-token check. This is proportionate for a small, admin-onboarded,
trusted audience — not a public multi-tenant SaaS.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from core.config import get_settings
from core.logging import get_logger
from core.memory.sqlite_store import _connect, _now

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    slug        TEXT PRIMARY KEY,
    owner_name  TEXT NOT NULL,
    pin_hash    TEXT NOT NULL,
    db_path     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def ensure_tenants_schema(path: str) -> str:
    """Create the tenant registry file (and parent dir) if missing. Idempotent.
    Returns the resolved absolute path."""
    resolved = str(Path(path).expanduser())
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(resolved)) as conn, conn:
        conn.executescript(_SCHEMA)
    log.info("tenants.schema_ready", path=resolved)
    return resolved


def _registry_path() -> str:
    return ensure_tenants_schema(get_settings().tenants_db_path)


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return base or "friend"


@dataclass(slots=True)
class Tenant:
    slug: str
    owner_name: str
    pin_hash: str
    db_path: str
    created_at: str


def _row_to_tenant(r) -> Tenant:
    return Tenant(
        slug=r["slug"],
        owner_name=r["owner_name"],
        pin_hash=r["pin_hash"],
        db_path=r["db_path"],
        created_at=r["created_at"],
    )


def _unique_slug(conn, base: str) -> str:
    """`base`, or `base-<short-random>` if taken, tried a few times."""
    candidate = base
    for _ in range(6):
        row = conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (candidate,)).fetchone()
        if row is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(2)}"
    # Exhausted retries (wildly unlikely) — fall back to a fully random slug.
    return f"{base}-{secrets.token_hex(4)}"


def generate_pin() -> str:
    """A 4-digit PIN, easy to type on a phone numeric pad."""
    return f"{secrets.randbelow(10000):04d}"


def create_tenant(
    owner_name: str,
    *,
    pin: str | None = None,
    slug: str | None = None,
    db_path: str | None = None,
) -> tuple[Tenant, str]:
    """Register a new person: a fresh slug + PIN + their own sqlite file.

    Returns `(tenant, plaintext_pin)` — the plaintext PIN only ever exists at
    creation time (only `pin_hash` is persisted), so callers must capture it
    here to hand it to the person.

    `db_path`, when given, lets a tenant point at an *existing* file (used to
    fold Khushi's already-live deployment into the registry without moving
    or resetting her data). Otherwise a new file is created under
    `settings.tenants_data_dir`.
    """
    from core.companion.profiles import ensure_profile_schema

    owner_name = owner_name.strip()
    if not owner_name:
        raise ValueError("owner_name is required")
    pin = pin if pin is not None else generate_pin()

    registry = _registry_path()
    with closing(_connect(registry)) as conn, conn:
        resolved_slug = slug or _unique_slug(conn, _slugify(owner_name))
        resolved_db_path = db_path or str(
            Path(get_settings().tenants_data_dir).expanduser() / f"{resolved_slug}.db"
        )
        # Initialize (or confirm) the tenant's own profiles+memory schema.
        ensure_profile_schema(resolved_db_path)

        now = _now()
        conn.execute(
            "INSERT INTO tenants (slug, owner_name, pin_hash, db_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (resolved_slug, owner_name, _hash_pin(pin), resolved_db_path, now),
        )

    tenant = Tenant(
        slug=resolved_slug, owner_name=owner_name, pin_hash=_hash_pin(pin),
        db_path=resolved_db_path, created_at=now,
    )
    log.info("tenants.created", slug=resolved_slug, owner_name=owner_name)
    return tenant, pin


def get_tenant_by_slug(slug: str) -> Tenant | None:
    if not slug or not _SLUG_OK.match(slug):
        return None
    with closing(_connect(_registry_path())) as conn:
        row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug,)).fetchone()
        return _row_to_tenant(row) if row else None


def get_tenant_by_db_path(db_path: str) -> Tenant | None:
    """Look up a tenant by its backing file — used to check whether the
    legacy single-owner deployment has already been folded into the registry."""
    resolved = str(Path(db_path).expanduser())
    with closing(_connect(_registry_path())) as conn:
        row = conn.execute("SELECT * FROM tenants WHERE db_path = ?", (resolved,)).fetchone()
        return _row_to_tenant(row) if row else None


def verify_pin(tenant: Tenant, pin: str) -> bool:
    if not pin:
        return False
    return hmac.compare_digest(_hash_pin(pin), tenant.pin_hash)


def list_tenants() -> list[Tenant]:
    with closing(_connect(_registry_path())) as conn:
        rows = conn.execute("SELECT * FROM tenants ORDER BY created_at DESC").fetchall()
        return [_row_to_tenant(r) for r in rows]


def delete_tenant(slug: str) -> bool:
    """Deprovision a person: drop the registry row AND their own sqlite file
    (memories, sessions, voice profile — everything). Irreversible. Returns
    False if the slug didn't exist."""
    tenant = get_tenant_by_slug(slug)
    if tenant is None:
        return False
    with closing(_connect(_registry_path())) as conn, conn:
        conn.execute("DELETE FROM tenants WHERE slug = ?", (slug,))
    db_file = Path(tenant.db_path)
    for suffix in ("", "-wal", "-shm"):
        f = db_file.with_name(db_file.name + suffix)
        if f.exists():
            f.unlink()
    log.info("tenants.deleted", slug=slug)
    return True
