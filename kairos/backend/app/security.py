"""Authentication, sessions, RBAC, CSRF protection and rate limiting."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Session_, User

_hasher = PasswordHasher()
settings = get_settings()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Sessions: opaque random tokens, only the SHA-256 is stored server side.
# ---------------------------------------------------------------------------


def create_session(db: Session, user: User, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        Session_(
            user_id=user.id,
            token_hash=sha256(token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
    )
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()  # native mobile clients
    return request.cookies.get(settings.session_cookie_name)


def _lookup(db: Session, token: str) -> tuple[Session_, User] | None:
    row = db.scalar(select(Session_).where(Session_.token_hash == sha256(token)))
    if row is None or row.revoked_at is not None:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if exp < datetime.now(UTC):
        return None
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    return row, user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    found = _lookup(db, token) if token else None
    if not found:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    sess, user = found
    now = datetime.now(UTC)
    last = user.last_seen_at
    if last is None or (now - (last if last.tzinfo else last.replace(tzinfo=UTC))).total_seconds() > 60:
        user.last_seen_at = now
        db.commit()
    request.state.user = user
    request.state.session_id = sess.id
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


def revoke_session(db: Session, request: Request) -> None:
    token = _token_from_request(request)
    if not token:
        return
    row = db.scalar(select(Session_).where(Session_.token_hash == sha256(token)))
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# CSRF: state-changing requests must carry a custom header. Browsers will not
# send custom headers cross-site without a CORS preflight, which we refuse.
# ---------------------------------------------------------------------------

CSRF_HEADER = "x-kairos-client"


def csrf_ok(request: Request) -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return True
    return bool(request.headers.get(CSRF_HEADER))


# ---------------------------------------------------------------------------
# Rate limiting: Redis fixed window when configured, in-memory fallback.
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, redis_url: str | None) -> None:
        self._redis = None
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(redis_url, socket_timeout=0.5)
                self._redis.ping()
            except Exception:  # noqa: BLE001 - degrade to in-memory
                self._redis = None
        self._mem: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    @property
    def backend(self) -> str:
        return "redis" if self._redis else "memory"

    def hit(self, key: str, limit: int, window: int) -> bool:
        """Record a hit; return False when the limit is exceeded."""
        if self._redis is not None:
            try:
                bucket = f"rl:{key}:{int(time.time() // window)}"
                n = self._redis.incr(bucket)
                if n == 1:
                    self._redis.expire(bucket, window)
                return n <= limit
            except Exception:  # noqa: BLE001
                pass
        now = time.time()
        with self._lock:
            hits = [h for h in self._mem[key] if h > now - window]
            hits.append(now)
            self._mem[key] = hits
            return len(hits) <= limit

    def reset(self) -> None:
        with self._lock:
            self._mem.clear()


limiter = RateLimiter(settings.redis_url)


def rate_limit(request: Request, scope: str, limit: int, window: int = 60, key: str | None = None) -> None:
    ident = key or client_ip(request)
    if not limiter.hit(f"{scope}:{ident}", limit, window):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please slow down.")
