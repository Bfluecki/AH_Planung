"""Login/Logout (Session-basiert, siehe app/auth.py)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth
from app.audit import log_action
from app.db import get_db

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
            {"request": request, "error": "Benutzername oder Passwort falsch."},
            status_code=401,
        )
    auth.login_session(request, user)
    log_action(db, user.username, "login")
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    username = request.session.get("username", "")
    if username:
        log_action(db, username, "logout")
    auth.logout_session(request)
    return RedirectResponse(url="/login", status_code=303)
