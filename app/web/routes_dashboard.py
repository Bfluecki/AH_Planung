"""Leichtes serverseitig gerendertes Dashboard (Konzept Abschnitt 6, Tech-Stack-Empfehlung)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.config import get_settings
from app.db import get_db
from app.domain.reporting import build_report

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


@router.get("/")
def dashboard(request: Request, year: int | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    effective = get_effective_config(db, settings)
    year = year or effective.current_planning_year
    year_summary, rows_by_month = build_report(db, year, settings)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "year": year,
            "year_summary": year_summary,
            "rows_by_month": rows_by_month,
            "month_names": MONTH_NAMES_DE,
        },
    )
