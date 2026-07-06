"""JSON-API der Planungsdaten (Konzept Abschnitt 5, Ausgabeformat "JSON-API")."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.reporting import build_report

router = APIRouter(prefix="/api/planning", tags=["planning"])


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


@router.get("/{year}")
def get_year(year: int, db: Session = Depends(get_db)) -> dict:
    year_summary, rows_by_month = build_report(db, year)
    months = []
    for m in year_summary.months:
        d = asdict(m)
        d["abweichung"] = m.abweichung
        d["zielerreichung_pct"] = m.zielerreichung_pct
        d["vorjahresvergleich_pct"] = m.vorjahresvergleich_pct
        months.append(_jsonable(d))
    return {
        "year": year,
        "budget_total": _jsonable(year_summary.budget),
        "umsatz_soll_total": _jsonable(year_summary.umsatz_soll),
        "umsatz_ist_total": _jsonable(year_summary.umsatz_ist),
        "abweichung_total": _jsonable(year_summary.abweichung),
        "zielerreichung_pct_total": _jsonable(year_summary.zielerreichung_pct),
        "months": months,
    }


@router.get("/{year}/{month}")
def get_month(year: int, month: int, db: Session = Depends(get_db)) -> dict:
    _, rows_by_month = build_report(db, year)
    rows = [_jsonable(asdict(r) | {"pax_diff": r.pax_diff, "umsatz_diff": r.umsatz_diff}) for r in rows_by_month.get(month, [])]
    return {"year": year, "month": month, "bookings": rows}
