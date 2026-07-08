"""Mehrjahres-Auswertungen fuer die Analyse-Seite (app/web/routes_analytics.py).

Baut auf build_report() auf, aggregiert aber ueber mehrere Jahre und liefert
KPI-Kennzahlen. Bewusst getrennt von reporting.py, das die Einzeljahres-Sicht fuer
Dashboard/Excel liefert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.reporting import build_report
from app.models import Booking, MonthlyAllocation


@dataclass
class YearFigures:
    year: int
    monthly_umsatz_ist: list[Decimal] = field(default_factory=list)   # 12 Werte
    monthly_umsatz_soll: list[Decimal] = field(default_factory=list)
    umsatz_ist: Decimal = Decimal("0")
    umsatz_soll: Decimal = Decimal("0")
    budget: Decimal = Decimal("0")
    pax_nights_ist: Decimal = Decimal("0")

    @property
    def zielerreichung_pct(self) -> Decimal:
        if self.budget == 0:
            return Decimal("0")
        return (self.umsatz_ist / self.budget * 100).quantize(Decimal("0.1"))


def available_years(db: Session) -> list[int]:
    rows = db.query(MonthlyAllocation.year).distinct().all()
    return sorted({r[0] for r in rows})


def year_figures(db: Session, year: int, settings: Settings | None = None) -> YearFigures:
    settings = settings or get_settings()
    year_summary, _ = build_report(db, year, settings, statuses=None)
    months = year_summary.months
    return YearFigures(
        year=year,
        monthly_umsatz_ist=[m.umsatz_ist for m in months],
        monthly_umsatz_soll=[m.umsatz_soll for m in months],
        umsatz_ist=year_summary.umsatz_ist,
        umsatz_soll=year_summary.umsatz_soll,
        budget=year_summary.budget,
        pax_nights_ist=year_summary.pax_nights_ist,
    )


@dataclass
class SollIstAccuracy:
    """Genauigkeit der Soll-Prognose: wie nah lag der bestellte (Soll-)Umsatz am
    effektiv verrechneten (Ist)? Nur ueber Buchungen mit beiden Werten."""

    booking_count: int
    mean_abs_deviation_pct: Decimal | None  # mittlere absolute Abweichung |Ist-Soll|/Soll


def soll_ist_accuracy(db: Session, year: int) -> SollIstAccuracy:
    bookings = (
        db.query(Booking)
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status == "verrechnet")
        .distinct()
        .all()
    )
    deviations: list[Decimal] = []
    for b in bookings:
        if b.umsatz_soll and b.umsatz_soll > 0 and b.umsatz_ist is not None:
            deviations.append(abs(b.umsatz_ist - b.umsatz_soll) / b.umsatz_soll * 100)
    if not deviations:
        return SollIstAccuracy(booking_count=len(bookings), mean_abs_deviation_pct=None)
    mean = (sum(deviations) / len(deviations)).quantize(Decimal("0.1"))
    return SollIstAccuracy(booking_count=len(bookings), mean_abs_deviation_pct=mean)
