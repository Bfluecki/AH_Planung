"""Auswertungsseite: grafischer Jahresvergleich + Performance-Kennzahlen."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.auth import User, require_login
from app.config import get_settings
from app.db import get_db
from app.domain.analytics import (
    accuracy_over_time,
    average_stats,
    available_years,
    cumulative_target,
    ertragsart_mix,
    monthly_occupancy,
    pipeline_value,
    soll_ist_accuracy,
    top_customers,
    year_figures,
)
from app.web.formatting import swissnum

router = APIRouter(tags=["analytics"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["swissnum"] = swissnum

MONTH_ABBR = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
_MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

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


def _build_bar_chart(monthly: list[tuple[int, Decimal]], max_val: float = 100.0) -> dict:
    """Balkendiagramm fuer 12 Monatswerte (z.B. Belegungsgrad in %)."""
    plot_w = _CHART_W - _PAD_L - _PAD_R
    plot_h = _CHART_H - _PAD_T - _PAD_B
    baseline_y = _PAD_T + plot_h
    max_val = max(max_val, max((float(v) for _, v in monthly), default=0.0)) or 1.0
    slot = plot_w / 12
    bar_w = slot * 0.62

    bars = []
    for i, (month, value) in enumerate(monthly):
        v = float(value)
        h = plot_h * (v / max_val)
        cx = _PAD_L + slot * i + slot / 2
        bars.append({
            "x": round(cx - bar_w / 2, 1),
            "y": round(baseline_y - h, 1),
            "w": round(bar_w, 1),
            "h": round(h, 1),
            "label": MONTH_ABBR[month - 1],
            "label_x": round(cx, 1),
            "value": value,
        })

    gridlines = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = _PAD_T + plot_h * (1 - frac)
        gridlines.append({"y": round(gy, 1), "label": swissnum(Decimal(str(max_val * frac)), 0)})

    return {
        "width": _CHART_W,
        "height": _CHART_H,
        "bars": bars,
        "gridlines": gridlines,
        "baseline_y": round(baseline_y, 1),
        "left_pad": _PAD_L,
        "right_x": _CHART_W - _PAD_R,
    }


def _build_labeled_bar_chart(
    items: list[tuple[str, Decimal | None]], max_val: float | None = None, unit: str = ""
) -> dict:
    """Balkendiagramm mit frei beschrifteten Balken (z.B. je Jahr). None-Werte werden
    als fehlend (kein Balken) behandelt. max_val=None -> automatische Skalierung."""
    plot_w = _CHART_W - _PAD_L - _PAD_R
    plot_h = _CHART_H - _PAD_T - _PAD_B
    baseline_y = _PAD_T + plot_h
    present = [float(v) for _, v in items if v is not None]
    scale = (max_val if max_val is not None else max(present, default=0.0)) or 1.0
    n = max(len(items), 1)
    slot = plot_w / n
    bar_w = slot * 0.5

    bars = []
    for i, (label, value) in enumerate(items):
        cx = _PAD_L + slot * i + slot / 2
        bar = {"label": label, "label_x": round(cx, 1), "missing": value is None}
        if value is not None:
            h = plot_h * (float(value) / scale)
            bar.update({
                "x": round(cx - bar_w / 2, 1),
                "y": round(baseline_y - h, 1),
                "w": round(bar_w, 1),
                "h": round(h, 1),
                "value_label": swissnum(Decimal(str(value)), 1) + unit,
            })
        bars.append(bar)

    gridlines = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = _PAD_T + plot_h * (1 - frac)
        gridlines.append({"y": round(gy, 1), "label": swissnum(Decimal(str(scale * frac)), 0) + unit})

    return {
        "width": _CHART_W,
        "height": _CHART_H,
        "bars": bars,
        "gridlines": gridlines,
        "baseline_y": round(baseline_y, 1),
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

    effective = get_effective_config(db, settings)
    occupancy = monthly_occupancy(db, current, effective.bed_capacity)
    occ_chart = _build_bar_chart([(o.month, o.occupancy_pct or Decimal("0")) for o in occupancy], max_val=100.0)
    customers = top_customers(db, current, limit=15)
    averages = average_stats(db, current)

    # Ertragsart-Mix: segmentierter Balken (Anteile) + Tabelle.
    mix = ertragsart_mix(db, current)
    mix_segments = [
        {
            "label": s.label,
            "pct": float(s.pct),
            "umsatz": s.umsatz,
            "pct_value": s.pct,
            "color": _LINE_COLORS[i % len(_LINE_COLORS)],
        }
        for i, s in enumerate(mix)
    ]

    # Soll-Ist-Genauigkeit im Zeitverlauf (kleiner = treffsicherer).
    acc_points = accuracy_over_time(db)
    acc_chart = _build_labeled_bar_chart(
        [(str(p.year), p.mean_abs_deviation_pct) for p in acc_points], unit="%"
    )

    # Pipeline-Wert (Angebote + Auftraege ohne Rechnung) je Monat.
    pipeline = pipeline_value(db, current)
    pipeline_chart = _build_bar_chart(
        [(m + 1, pipeline.monthly_soll[m]) for m in range(12)], max_val=0.0
    )

    # Kumulierte Zielerreichung: kumulierter Ist-Umsatz vs. kumuliertes Budget.
    cumulative = cumulative_target(db, current, settings)
    cum_chart = _build_line_chart([
        ("Budget (kumuliert)", [p.cum_budget for p in cumulative.points]),
        ("Umsatz Ist (kumuliert)", [p.cum_ist for p in cumulative.points]),
    ])

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
            "bed_capacity": effective.bed_capacity,
            "occupancy": occupancy,
            "occ_chart": occ_chart,
            "customers": customers,
            "averages": averages,
            "month_abbr": MONTH_ABBR,
            "mix": mix,
            "mix_segments": mix_segments,
            "acc_points": acc_points,
            "acc_chart": acc_chart,
            "pipeline": pipeline,
            "pipeline_chart": pipeline_chart,
            "cumulative": cumulative,
            "cum_chart": cum_chart,
            "month_names": _MONTH_NAMES_DE,
        },
    )
