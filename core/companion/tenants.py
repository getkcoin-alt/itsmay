"""Per-person Mini tenant registry.

A tenant gets an unguessable URL slug, a PIN whose plaintext is returned only at
creation time, and a dedicated SQLite database path under the configured tenant
data directory.  The registry stores only a salted scrypt verifier for the PIN.

This module is intentionally synchronous: tenant administration is tiny and the
router calls it from short request paths.  It never opens a tenant's Mini DB;
that remains CompanionEngine's job.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.config import get_settings

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_PIN_RE = re.compile(r"^[0-9]{4,12}$")
_SALT_BYTES = 16
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_CREATE_RETRIES = 8

# A four-digit gift PIN is intentionally convenient, not high entropy.  The
# expensive verifier protects a stolen registry; this small process-local
# throttle also makes online guessing materially slower without changing the
# router API.  It is defense in depth, not a substitute for an upstream rate
# limiter.
_FAILURE_WINDOW_SECONDS = 300.0
_LOCK_SECONDS = 300.0
_MAX_FAILURES = 5
_failures: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}


@dataclass(slots=True, frozen=True)
class Tenant:
    slug: str
    owner_name: str
    db_path: str
    pin_salt: bytes
    pin_hash: bytes
    created_at: str


def _registry_path() -> Path:
    return Path(get_settings().tenants_db_path).expanduser().resolve()


def _data_dir() -> Path:
    return Path(get_settings().tenants_data_dir).expanduser().resolve()


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _secure_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _connect() -> sqlite3.Connection:
    path = _registry_path()
    _secure_dir(path.parent)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mini_tenants (
            slug       TEXT PRIMARY KEY,
            owner_name TEXT NOT NULL,
            db_path    TEXT NOT NULL UNIQUE,
            pin_salt   BLOB NOT NULL,
            pin_hash   BLOB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    _secure_file(path)
    return conn


def _hash_pin(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )


def _validate_pin(pin: str) -> str:
    value = str(pin or "").strip()
    if not _PIN_RE.fullmatch(value):
        raise ValueError("PIN must contain 4 to 12 digits")
    return value


def _new_pin() -> str:
    return f"{secrets.randbelow(10_000):04d}"


def _new_slug() -> str:
    # 144 bits of randomness while remaining URL/path safe.
    return secrets.token_urlsafe(18).rstrip("=")


def _tenant_db_path(slug: str) -> Path:
    data = _data_dir()
    _secure_dir(data)
    candidate = (data / f"{slug}.db").resolve()
    if not candidate.is_relative_to(data):
        raise ValueError("tenant database path escaped configured data directory")
    return candidate


def _row_to_tenant(row: sqlite3.Row | None) -> Tenant | None:
    if row is None:
        return None
    return Tenant(
        slug=str(row["slug"]),
        owner_name=str(row["owner_name"]),
        db_path=str(row["db_path"]),
        pin_salt=bytes(row["pin_salt"]),
        pin_hash=bytes(row["pin_hash"]),
        created_at=str(row["created_at"]),
    )


def create_tenant(owner_name: str, *, pin: str | None = None) -> tuple[Tenant, str]:
    """Create an isolated tenant and return its one-time plaintext PIN."""

    owner = str(owner_name or "").strip()
    if not owner:
        raise ValueError("owner_name is required")
    if len(owner) > 200:
        raise ValueError("owner_name is too long")
    plaintext = _validate_pin(pin) if pin is not None else _new_pin()
    salt = secrets.token_bytes(_SALT_BYTES)
    verifier = _hash_pin(plaintext, salt)
    created_at = datetime.now(UTC).isoformat()

    with closing(_connect()) as conn:
        for _ in range(_MAX_CREATE_RETRIES):
            slug = _new_slug()
            if not _SLUG_RE.fullmatch(slug):  # defensive against implementation changes
                continue
            db_path = _tenant_db_path(slug)
            try:
                conn.execute(
                    """
                    INSERT INTO mini_tenants
                        (slug, owner_name, db_path, pin_salt, pin_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (slug, owner, str(db_path), salt, verifier, created_at),
                )
                conn.commit()
                tenant = Tenant(slug, owner, str(db_path), salt, verifier, created_at)
                return tenant, plaintext
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("could not allocate a unique Mini tenant slug")


def get_tenant_by_slug(slug: str) -> Tenant | None:
    value = str(slug or "").strip()
    if not _SLUG_RE.fullmatch(value):
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            """
            SELECT slug, owner_name, db_path, pin_salt, pin_hash, created_at
            FROM mini_tenants WHERE slug = ?
            """,
            (value,),
        ).fetchone()
    return _row_to_tenant(row)


def list_tenants() -> list[Tenant]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT slug, owner_name, db_path, pin_salt, pin_hash, created_at
            FROM mini_tenants ORDER BY created_at ASC
            """
        ).fetchall()
    return [tenant for row in rows if (tenant := _row_to_tenant(row)) is not None]


def _is_locked(slug: str, now: float) -> bool:
    until = _locked_until.get(slug, 0.0)
    if until > now:
        return True
    if until:
        _locked_until.pop(slug, None)
    return False


def _record_failure(slug: str, now: float) -> None:
    recent = [t for t in _failures.get(slug, []) if now - t <= _FAILURE_WINDOW_SECONDS]
    recent.append(now)
    if len(recent) >= _MAX_FAILURES:
        _locked_until[slug] = now + _LOCK_SECONDS
        _failures.pop(slug, None)
    else:
        _failures[slug] = recent


def verify_pin(tenant: Tenant, presented_pin: str) -> bool:
    """Verify one tenant PIN in constant time and throttle repeated failures."""

    now = time.monotonic()
    if _is_locked(tenant.slug, now):
        return False
    presented = str(presented_pin or "").strip()
    # Still perform one expensive hash for malformed guesses to avoid creating a
    # cheap remote oracle that distinguishes PIN shape from a wrong PIN.
    candidate = presented if _PIN_RE.fullmatch(presented) else "0" * 12
    digest = _hash_pin(candidate, tenant.pin_salt)
    ok = bool(_PIN_RE.fullmatch(presented)) and hmac.compare_digest(digest, tenant.pin_hash)
    if ok:
        _failures.pop(tenant.slug, None)
        _locked_until.pop(tenant.slug, None)
        return True
    _record_failure(tenant.slug, now)
    return False


def _safe_registered_db_path(tenant: Tenant) -> Path:
    root = _data_dir()
    candidate = Path(tenant.db_path).expanduser().resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("registered tenant database path is outside configured data directory")
    expected = _tenant_db_path(tenant.slug)
    if candidate != expected:
        raise ValueError("registered tenant database path does not match tenant slug")
    return candidate


def delete_tenant(slug: str) -> bool:
    """Delete registry state and the tenant's isolated SQLite files.

    Registry deletion happens only after the stored DB path passes containment
    validation, so a tampered registry row cannot turn deprovisioning into an
    arbitrary-file deletion primitive.
    """

    tenant = get_tenant_by_slug(slug)
    if tenant is None:
        return False
    db_path = _safe_registered_db_path(tenant)

    with closing(_connect()) as conn:
        cursor = conn.execute("DELETE FROM mini_tenants WHERE slug = ?", (tenant.slug,))
        conn.commit()
        deleted = cursor.rowcount == 1
    if not deleted:
        return False

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The registry is already gone. Surface the cleanup failure rather
            # than silently claiming a complete deprovision.
            raise
    _failures.pop(tenant.slug, None)
    _locked_until.pop(tenant.slug, None)
    return True


__all__ = [
    "Tenant",
    "create_tenant",
    "delete_tenant",
    "get_tenant_by_slug",
    "list_tenants",
    "verify_pin",
]
