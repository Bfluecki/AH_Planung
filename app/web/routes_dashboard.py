"""Leichtes serverseitig gerendertes Dashboard (Konzept Abschnitt 6, Tech-Stack-Empfehlung)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.audit import log_action
from app.auth import ROLE_ADMIN, User, require_login
from app.budget import save_budget_override
from app.config import get_settings
from app.db import get_db
from app.domain.reporting import build_report
from app.domain.week_calendar import (
    STATUS_SYMBOLS,
    WEEKDAY_NAMES,
    calendar_summary,
    calendar_weeks,
)
from app.domain.soll_ist import (
    STATUS_ABGELEHNT,
    STATUS_BEAUFTRAGT,
    STATUS_NUR_ANGEBOT,
    STATUS_STORNIERT,
    STATUS_VERRECHNET,
)
from app.web.flash import pop_flash
from app.web.navigation import nav_context
from app.web.formatting import swissnum
from app.web.assets import register as register_assets

router = APIRouter(tags=["dashboard"])
templates = register_assets(Jinja2Templates(directory=str(Path(__file__).parent / "templates")))
templates.env.filters["swissnum"] = swissnum

MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

# Reihenfolge fuer die Filter-Checkboxen. Als Planungswerkzeug zeigt die Ansicht
# standardmaessig alles, was zum Plan beitraegt - Angebote, Auftraege und bereits
# Verrechnetes. Abgelehntes und Storniertes ist ausgeblendet und zuschaltbar: beides
# traegt keinen Umsatz mehr bei und wuerde die Planung sonst zu optimistisch machen.
ALL_STATUSES = [
    STATUS_NUR_ANGEBOT,
    STATUS_BEAUFTRAGT,
    STATUS_VERRECHNET,
    STATUS_ABGELEHNT,
    STATUS_STORNIERT,
]
DEFAULT_STATUSES = [STATUS_NUR_ANGEBOT, STATUS_BEAUFTRAGT, STATUS_VERRECHNET]
# Der Kalender ist ein Planungsinstrument: er zeigt von Haus aus, was noch
# ansteht. Verrechnetes (= Vergangenheit) ist ueber den Filter zuschaltbar.
CALENDAR_DEFAULT_STATUSES = [STATUS_NUR_ANGEBOT, STATUS_BEAUFTRAGT]


def _gewaehlte_status(status: list[str] | None, filter_aktiv: str, vorgabe: list[str]) -> set[str]:
    """Welche Status sollen angezeigt werden?

    Ein Formular schickt abgewaehlte Checkboxen gar nicht mit. "Nichts angehakt"
    waere daher von "Seite zum ersten Mal geoeffnet" nicht zu unterscheiden - das
    versteckte Feld filter=1 trennt die beiden Faelle. Wer bewusst alles abwaehlt,
    bekommt eine leere Liste und nicht die Vorgabe zurueck.
    """
    if status:
        return set(status)
    if filter_aktiv:
        return set()
    return set(vorgabe)


@router.get("/")
def dashboard(
    request: Request,
    year: int | None = None,
    status: list[str] | None = Query(default=None),
    q: str = "",
    filter_aktiv: str = Query("", alias="filter"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    settings = get_settings()
    effective = get_effective_config(db, settings)
    year = year or effective.current_planning_year
    selected_statuses = _gewaehlte_status(status, filter_aktiv, DEFAULT_STATUSES)
    search = q.strip()

    # Status- und Suchfilter wirken auf Jahresuebersicht UND Detailzeilen gleichermassen.
    year_summary, rows_by_month = build_report(
        db, year, settings, statuses=selected_statuses, search=search or None
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "year": year,
            "year_summary": year_summary,
            "rows_by_month": rows_by_month,
            "month_names": MONTH_NAMES_DE,
            "flash": pop_flash(request),
            **nav_context(
                db, user, year, "dashboard",
                ziel_url="/", mit_filter=True,
                selected_statuses=selected_statuses,
                default_statuses=set(DEFAULT_STATUSES),
                search=search,
            ),
        },
    )


@router.get("/hilfe")
def hilfe(
    request: Request,
    year: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    year = year or get_effective_config(db, get_settings()).current_planning_year
    return templates.TemplateResponse(
        "hilfe.html",
        {"request": request, **nav_context(db, user, year, "hilfe", mit_jahr=False)},
    )


@router.post("/budget/save")
def save_budget(
    year: int = Form(...),
    month: int = Form(...),
    budget_chf: str = Form(...),
    status: list[str] = Form(default=DEFAULT_STATUSES),
    q: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    try:
        value = Decimal(budget_chf.replace("'", "").strip())
    except InvalidOperation:
        value = None
    if value is not None:
        save_budget_override(db, year, month, value)
        log_action(db, user.username, "budget_change", f"{year}-{month:02d} = {value}")
    params = [("year", year)] + [("status", s) for s in status]
    if q.strip():
        params.append(("q", q.strip()))
    return RedirectResponse(url=f"/?{urlencode(params)}", status_code=303)


@router.get("/kalender")
def kalender(
    request: Request,
    year: int | None = None,
    status: list[str] | None = Query(default=None),
    q: str = "",
    filter_aktiv: str = Query("", alias="filter"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Belegungsplanung als Wochenkalender - Anlaesse farblich nach Status."""
    settings = get_settings()
    effective = get_effective_config(db, settings)
    year = year or effective.current_planning_year
    selected_statuses = _gewaehlte_status(status, filter_aktiv, CALENDAR_DEFAULT_STATUSES)
    search = q.strip()

    wochen = calendar_weeks(db, year, selected_statuses, search=search or None)
    return templates.TemplateResponse(
        "kalender.html",
        {
            "request": request,
            "year": year,
            "wochen": wochen,
            "zahlen": calendar_summary(wochen),
            "weekday_names": WEEKDAY_NAMES,
            "status_symbols": STATUS_SYMBOLS,
            **nav_context(
                db, user, year, "kalender",
                ziel_url="/kalender", mit_filter=True,
                selected_statuses=selected_statuses,
                default_statuses=set(CALENDAR_DEFAULT_STATUSES),
                search=search,
            ),
        },
    )
