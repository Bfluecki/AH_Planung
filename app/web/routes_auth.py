"""Login/Logout (Session-basiert, siehe app/auth.py)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth
from app.audit import log_action
from app.auth import User, require_login
from app.db import get_db

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Mindestlaenge fuer selbst vergebene Passwoerter.
_MIN_PASSWORD_LEN = 8


@router.get("/login")
def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.authenticate(db, username, password)
    if user is None:
        log_action(db, username, "login_failed")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "E-Mail-Adresse oder Passwort falsch."},
            status_code=401,
        )
    auth.login_session(request, user)
    log_action(db, user.username, "login")
    return RedirectResponse(url="/", status_code=303)


@router.get("/passwort")
def password_form(request: Request, user: User = Depends(require_login)):
    return templates.TemplateResponse(
        "passwort.html",
        {
            "request": request,
            "current_user": user.username,
            "error": None,
            "success": False,
            "forced": user.must_change_password,
        },
    )


@router.post("/passwort")
def password_change(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    def render(error: str | None = None, success: bool = False):
        return templates.TemplateResponse(
            "passwort.html",
            {
                "request": request,
                "current_user": user.username,
                "error": error,
                "success": success,
                "forced": user.must_change_password and not success,
            },
            status_code=200 if success else 400,
        )

    if not auth.verify_password(current_password, user.password_hash):
        log_action(db, user.username, "password_self_change_failed", "aktuelles Passwort falsch")
        return render("Das aktuelle Passwort ist falsch.")
    if len(new_password) < _MIN_PASSWORD_LEN:
        return render(f"Das neue Passwort muss mindestens {_MIN_PASSWORD_LEN} Zeichen lang sein.")
    if new_password != confirm_password:
        return render("Die beiden neuen Passwörter stimmen nicht überein.")
    if new_password == current_password:
        return render("Das neue Passwort muss sich vom bisherigen unterscheiden.")

    user.password_hash = auth.hash_password(new_password)
    user.must_change_password = False
    db.commit()
    log_action(db, user.username, "password_self_change")
    return render(success=True)


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    username = request.session.get("username", "")
    if username:
        log_action(db, username, "logout")
    auth.logout_session(request)
    return RedirectResponse(url="/login", status_code=303)
