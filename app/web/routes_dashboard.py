"""Leichtes serverseitig gerendertes Dashboard (Konzept Abschnitt 6, Tech-Stack-Empfehlung)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.budget import save_budget_override
from app.config import get_settings
from app.db import get_db
from app.domain.reporting import build_report
from app.domain.soll_ist import (
    STATUS_BEAUFTRAGT,
    STATUS_NUR_ANGEBOT,
    STATUS_STORNIERT,
    STATUS_VERRECHNET,
)
from app.web.formatting import swissnum

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["swissnum"] = swissnum

MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

# Reihenfolge fuer die Filter-Checkboxen; Default zeigt nur verrechnete Buchungen,
# die uebrigen Status sind ueber das Dropdown zuwaehlbar.
ALL_STATUSES = [STATUS_NUR_ANGEBOT, STATUS_BEAUFTRAGT, STATUS_VERRECHNET, STATUS_STORNIERT]
DEFAULT_STATUSES = [STATUS_VERRECHNET]


@router.get("/")
def dashboard(
    request: Request,
    year: int | None = None,
    status: list[str] = Query(default=DEFAULT_STATUSES),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    effective = get_effective_config(db, settings)
    year = year or effective.current_planning_year
    selected_statuses = set(status) or set(DEFAULT_STATUSES)

    # Status-Filter wirkt auf Jahresuebersicht UND Detailzeilen gleichermassen.
    year_summary, rows_by_month = build_report(db, year, settings, statuses=selected_statuses)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "year": year,
            "year_summary": year_summary,
            "rows_by_month": rows_by_month,
            "month_names": MONTH_NAMES_DE,
            "all_statuses": ALL_STATUSES,
            "selected_statuses": selected_statuses,
        },
    )


@router.post("/budget/save")
def save_budget(
    year: int = Form(...),
    month: int = Form(...),
    budget_chf: str = Form(...),
    status: list[str] = Form(default=DEFAULT_STATUSES),
    db: Session = Depends(get_db),
):
    try:
        value = Decimal(budget_chf.replace("'", "").strip())
    except InvalidOperation:
        value = None
    if value is not None:
        save_budget_override(db, year, month, value)
    query = urlencode([("year", year)] + [("status", s) for s in status])
    return RedirectResponse(url=f"/?{query}", status_code=303)
