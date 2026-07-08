"""Benutzer-Authentifizierung: Passwort-Hashing, User-CRUD und Session-Dependencies.

Session-basiert (signiertes Cookie via Starlette SessionMiddleware, siehe app/main.py).
Passwoerter werden als PBKDF2-HMAC-SHA256 mit zufaelligem Salt gespeichert - stdlib-only,
keine zusaetzliche Krypto-Abhaengigkeit.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

ROLE_ADMIN = "admin"
ROLE_USER = "user"
_PBKDF2_ROUNDS = 200_000


# ------------------------------------------------------------------ Hashing
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ User-CRUD
def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).one_or_none()


def create_user(db: Session, username: str, password: str, role: str = ROLE_USER) -> User:
    user = User(username=username.strip(), password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username.strip())
    if user and user.is_active and verify_password(password, user.password_hash):
        return user
    return None


def ensure_seed_admin(db: Session) -> None:
    """Legt beim ersten Start einen Admin aus ADMIN_USERNAME/ADMIN_PASSWORD an, falls
    noch gar keine Benutzer existieren - so bleibt ein bestehendes Deployment ohne
    manuellen Schritt anmeldbar."""
    if db.query(User).count() > 0:
        return
    from app.config import get_settings

    settings = get_settings()
    if not settings.admin_password:
        return
    create_user(db, settings.admin_username or "admin", settings.admin_password, role=ROLE_ADMIN)


# ------------------------------------------------------------------ Session
def login_session(request: Request, user: User) -> None:
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    # Rotationsschutz gegen Session-Fixation.
    request.session["nonce"] = secrets.token_hex(8)


def logout_session(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


class _Redirect(HTTPException):
    """Nicht angemeldet -> auf Login umleiten (statt 401-JSON)."""

    def __init__(self, location: str = "/login"):
        super().__init__(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": location})


def require_login(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise _Redirect("/login")
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur für Administratoren")
    return user
