from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Account, KycRecord, PasswordResetToken, Session_, User
from ..security import (
    clear_session_cookie,
    client_ip,
    create_session,
    current_user,
    hash_password,
    rate_limit,
    revoke_session,
    set_session_cookie,
    sha256,
    verify_password,
)
from ..seed import seed_fresh_account
from ..serializers import user_json
from ..services.audit import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

# Pragmatic email check (accepts reserved demo TLDs such as .test).
EmailStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
]

PASSWORD_RULE = "At least 8 characters with upper- and lower-case letters, a number and a symbol"


def _strong(pw: str) -> str:
    if (
        len(pw) < 8
        or not re.search(r"[A-Z]", pw)
        or not re.search(r"[a-z]", pw)
        or not re.search(r"\d", pw)
        or not re.search(r"[^A-Za-z0-9]", pw)
    ):
        raise ValueError(PASSWORD_RULE)
    return pw


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(max_length=128)
    phone: str | None = Field(default=None, max_length=20)
    accept_terms: bool

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)

    @field_validator("accept_terms")
    @classmethod
    def _terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must acknowledge the demo terms")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=128)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(max_length=128)

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _strong(v)


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(request, "register", 5, 300)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, full_name=body.full_name.strip(), phone=body.phone,
                password_hash=hash_password(body.password), role="user")
    db.add(user)
    db.flush()
    db.add(Account(user_id=user.id))
    db.add(KycRecord(user_id=user.id))
    audit(db, "auth.register", user, "user", user.id, ip=client_ip(request))
    db.commit()
    seed_fresh_account(db, user)
    token = create_session(db, user, request)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    set_session_cookie(response, token)
    return {"user": user_json(user), "token": token}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    rate_limit(request, "login-ip", 20, 60)
    rate_limit(request, "login-email", 8, 300, key=email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(user.password_hash, body.password):
        audit(db, "auth.login_failed", None, "user", user.id if user else None, {"email": email}, client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    if user.status != "active":
        raise HTTPException(403, "This account has been disabled by an administrator")
    token = create_session(db, user, request)
    user.last_login_at = user.last_seen_at = datetime.now(UTC)
    audit(db, "auth.login", user, "user", user.id, ip=client_ip(request))
    db.commit()
    set_session_cookie(response, token)
    return {"user": user_json(user), "token": token}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    revoke_session(db, request)
    db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": user_json(user)}


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "forgot", 5, 300)
    email = body.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    out: dict = {
        "ok": True,
        "message": "If an account exists for that email, a password reset link has been issued.",
    }
    if user is None or user.status != "active":
        return out
    if email in (settings.demo_user_email, settings.admin_email):
        out["message"] = "The shared demonstration accounts cannot be reset. Register your own demo account instead."
        return out
    token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=sha256(token),
                              expires_at=datetime.now(UTC) + timedelta(minutes=30)))
    audit(db, "auth.password_reset_requested", user, "user", user.id, ip=client_ip(request))
    db.commit()
    if settings.expose_reset_links:
        # The demo has no outbound email; surface the link instead of mailing it.
        out["demo_reset_link"] = f"/forgot-password?token={token}"
    return out


@router.post("/reset-password")
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request, "reset", 10, 300)
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == sha256(body.token)))
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) < now:
        raise HTTPException(400, "This reset link is invalid or has expired")
    user = db.get(User, row.user_id)
    user.password_hash = hash_password(body.password)
    row.used_at = now
    for s in db.scalars(select(Session_).where(Session_.user_id == user.id, Session_.revoked_at.is_(None))):
        s.revoked_at = now
    audit(db, "auth.password_reset", user, "user", user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.email in (settings.demo_user_email, settings.admin_email):
        raise HTTPException(403, "Passwords of the shared demonstration accounts cannot be changed")
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit(db, "auth.password_changed", user, "user", user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
