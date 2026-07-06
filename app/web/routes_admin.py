"""Admin-Seite: Bexio-Zugangsdaten und Planungsparameter zur Laufzeit verwalten.

Geschuetzt durch HTTP Basic Auth (siehe app/web/auth.py). Das Bexio-Client-Secret
wird nie im Klartext an den Browser zurueckgegeben - das Formularfeld ist immer
leer und ein Absenden ohne Eingabe laesst den gespeicherten Wert unveraendert.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config, save_admin_config
from app.config import get_settings
from app.db import get_db
from app.domain.allocation import ALLOCATION_MODE_FULL_MONTH, ALLOCATION_MODE_PRORATA
from app.models import OAuthToken
from app.web.auth import require_admin_auth

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_auth)])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _context(request: Request, db: Session, message: str | None = None) -> dict:
    settings = get_settings()
    effective = get_effective_config(db, settings)
    token = db.get(OAuthToken, 1)
    return {
        "request": request,
        "message": message,
        "effective": effective,
        "redirect_uri": settings.bexio_redirect_uri,
        "token": token,
        "allocation_modes": [ALLOCATION_MODE_PRORATA, ALLOCATION_MODE_FULL_MONTH],
    }


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db)):
    message = None
    if request.query_params.get("saved"):
        message = "Gespeichert."
    elif request.query_params.get("cleared"):
        message = "Bexio-Zugangsdaten-Override entfernt, Env-Variablen gelten wieder."
    return templates.TemplateResponse("admin.html", _context(request, db, message))


@router.post("/save")
def admin_save(
    request: Request,
    bexio_client_id: str = Form(""),
    bexio_client_secret: str = Form(""),
    allocation_mode: str = Form(ALLOCATION_MODE_PRORATA),
    monthly_budget_chf: str = Form(""),
    current_planning_year: str = Form(""),
    db: Session = Depends(get_db),
):
    budget = None
    if monthly_budget_chf.strip():
        try:
            budget = Decimal(monthly_budget_chf.strip())
        except InvalidOperation:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, "Ungueltiges Budget-Format.")
            )

    year = None
    if current_planning_year.strip():
        try:
            year = int(current_planning_year.strip())
        except ValueError:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, "Ungueltiges Jahr-Format.")
            )

    save_admin_config(
        db,
        bexio_client_id=bexio_client_id.strip() or None,
        bexio_client_secret=bexio_client_secret.strip() or None,
        allocation_mode=allocation_mode,
        monthly_budget_chf=budget,
        current_planning_year=year,
    )
    return RedirectResponse(url="/admin?saved=1", status_code=303)


@router.post("/clear-bexio-credentials")
def admin_clear_bexio(db: Session = Depends(get_db)):
    save_admin_config(db, clear_bexio_credentials=True)
    return RedirectResponse(url="/admin?cleared=1", status_code=303)
