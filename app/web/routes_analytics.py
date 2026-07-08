"""Auswertungsseite: grafischer Jahresvergleich + Performance-Kennzahlen."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import User, require_login
from app.config import get_settings
from app.db import get_db
from app.domain.analytics import available_years, soll_ist_accuracy, year_figures
from app.web.formatting import swissnum

router = APIRouter(tags=["analytics"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["swissnum"] = swissnum

MONTH_ABBR = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

# Farbpalette fuer die Jahreslinien (barrierearm, gut unterscheidbar).
_LINE_COLORS = ["#7a2733", "#b08a4f", "#3f7a4f", "#4a6fa5", "#8a5a2b", "#6b6459"]

_CHART_W = 720
_CHART_H = 300
_PAD_L = 60
_PAD_B = 30
_PAD_T = 15
_PAD_R = 15


def _build_line_chart(years_data: list[tuple[int, list[Decimal]]]) -> dict:
    """Berechnet SVG-Polyline-Punkte fuer den Monats-Jahresvergleich.

    years_data: Liste (Jahr, 12 Monatswerte). Gibt Punkte/Beschriftungen fuers
    Template zurueck - kein externes Chart-Framework noetig (CSP-sicher)."""
    all_values = [float(v) for _, series in years_data for v in series]
    max_val = max(all_values, default=0.0) or 1.0

    plot_w = _CHART_W - _PAD_L - _PAD_R
    plot_h = _CHART_H - _PAD_T - _PAD_B

    def x(month_idx: int) -> float:
        return _PAD_L + (plot_w * month_idx / 11)

    def y(value: float) -> float:
        return _PAD_T + plot_h * (1 - value / max_val)

    series = []
    for idx, (yr, values) in enumerate(years_data):
        points = " ".join(f"{x(i):.1f},{y(float(v)):.1f}" for i, v in enumerate(values))
        series.append({"year": yr, "color": _LINE_COLORS[idx % len(_LINE_COLORS)], "points": points})

    # Y-Achsen-Gitterlinien (0, 25, 50, 75, 100 %)
    gridlines = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = _PAD_T + plot_h * (1 - frac)
        gridlines.append({"y": round(gy, 1), "label": swissnum(Decimal(str(max_val * frac)), 0)})

    xlabels = [{"x": round(x(i), 1), "label": MONTH_ABBR[i]} for i in range(12)]

    return {
        "width": _CHART_W,
        "height": _CHART_H,
        "series": series,
        "gridlines": gridlines,
        "xlabels": xlabels,
        "baseline_y": round(_PAD_T + plot_h, 1),
        "left_pad": _PAD_L,
        "right_x": _CHART_W - _PAD_R,
    }


@router.get("/auswertung")
def auswertung(
    request: Request,
    year: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    settings = get_settings()
    years = available_years(db)
    current = year or settings.current_planning_year
    if years and current not in years:
        current = years[-1]

    figures = {yr: year_figures(db, yr, settings) for yr in years}
    current_fig = figures.get(current) or year_figures(db, current, settings)

    # Jahresvergleich: Umsatz Ist pro Monat, alle verfuegbaren Jahre als Linien.
    chart_years = [(yr, figures[yr].monthly_umsatz_ist) for yr in years] or [
        (current, current_fig.monthly_umsatz_ist)
    ]
    chart = _build_line_chart(chart_years)

    # KPIs
    prev_year = current - 1
    prev_ist = figures[prev_year].umsatz_ist if prev_year in figures else None
    yoy_pct = None
    if prev_ist and prev_ist > 0:
        yoy_pct = ((current_fig.umsatz_ist - prev_ist) / prev_ist * 100).quantize(Decimal("0.1"))
    accuracy = soll_ist_accuracy(db, current)

    return templates.TemplateResponse(
        "auswertung.html",
        {
            "request": request,
            "current_user": user.username,
            "year": current,
            "years": years,
            "chart": chart,
            "current_fig": current_fig,
            "yoy_pct": yoy_pct,
            "prev_year": prev_year,
            "accuracy": accuracy,
        },
    )
