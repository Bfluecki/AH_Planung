"""Mehrjahres-Auswertungen fuer die Analyse-Seite (app/web/routes_analytics.py).

Baut auf build_report() auf, aggregiert aber ueber mehrere Jahre und liefert
KPI-Kennzahlen. Bewusst getrennt von reporting.py, das die Einzeljahres-Sicht fuer
Dashboard/Excel liefert.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.reporting import build_report
from app.domain.soll_ist import STATUS_VERRECHNET
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


# ------------------------------------------------------------------ Auslastung
@dataclass
class MonthOccupancy:
    month: int
    pax_nights_ist: Decimal
    capacity: int            # Betten x Kalendernaechte des Monats
    occupancy_pct: Decimal | None  # None wenn keine Kapazitaet konfiguriert


def monthly_occupancy(db: Session, year: int, bed_capacity: int) -> list[MonthOccupancy]:
    """Belegungsgrad je Monat = verrechnete PAX-Naechte / (Betten x Naechte im Monat).

    bed_capacity<=0 -> Kapazitaet unbekannt, occupancy_pct bleibt None."""
    rows = (
        db.query(MonthlyAllocation.month, func.sum(MonthlyAllocation.pax_nights_ist))
        .filter(MonthlyAllocation.year == year)
        .group_by(MonthlyAllocation.month)
        .all()
    )
    by_month = {int(m): Decimal(str(v or 0)) for m, v in rows}
    result: list[MonthOccupancy] = []
    for month in range(1, 13):
        pax_nights = by_month.get(month, Decimal("0"))
        nights_in_month = calendar.monthrange(year, month)[1]
        capacity = bed_capacity * nights_in_month if bed_capacity > 0 else 0
        occ = None
        if capacity > 0:
            occ = (pax_nights / capacity * 100).quantize(Decimal("0.1"))
        result.append(MonthOccupancy(month, pax_nights, capacity, occ))
    return result


# ------------------------------------------------------------------ Top-Kunden
@dataclass
class CustomerStat:
    kunde: str
    umsatz_ist: Decimal
    booking_count: int
    is_returning: bool  # kommt in mehreren Jahren vor


def top_customers(db: Session, year: int, limit: int = 15) -> list[CustomerStat]:
    """Top-Kunden nach verrechnetem Umsatz im Leistungsjahr. is_returning = Kunde
    hat auch in einem anderen Jahr mindestens eine Buchung (Wiederkehrer)."""
    # Umsatz Ist je Kunde im Jahr (nur verrechnete Buchungen).
    rows = (
        db.query(
            Booking.kunde,
            func.sum(MonthlyAllocation.umsatz_ist),
            func.count(func.distinct(Booking.id)),
        )
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .group_by(Booking.kunde)
        .all()
    )
    # Kunden mit Buchungen in anderen Jahren (fuer Wiederkehrer-Flag).
    returning = {
        k
        for (k,) in db.query(func.distinct(Booking.kunde))
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year != year)
        .all()
    }
    stats = [
        CustomerStat(
            kunde=kunde or "(ohne Namen)",
            umsatz_ist=Decimal(str(umsatz or 0)),
            booking_count=int(cnt),
            is_returning=(kunde in returning),
        )
        for kunde, umsatz, cnt in rows
    ]
    stats.sort(key=lambda s: s.umsatz_ist, reverse=True)
    return stats[:limit]


# ------------------------------------------------------------------ Durchschnitte
@dataclass
class AverageStats:
    booking_count: int
    umsatz_ist_total: Decimal
    pax_nights_total: Decimal
    avg_per_booking: Decimal | None
    avg_per_pax_night: Decimal | None


def average_stats(db: Session, year: int) -> AverageStats:
    """Durchschnittlicher Umsatz pro Anlass (Buchung) und pro PAX-Nacht - nur
    verrechnete Buchungen im Leistungsjahr."""
    umsatz_total, booking_count = (
        db.query(func.sum(MonthlyAllocation.umsatz_ist), func.count(func.distinct(Booking.id)))
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .one()
    )
    pax_total = (
        db.query(func.sum(MonthlyAllocation.pax_nights_ist))
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .scalar()
    )
    umsatz_total = Decimal(str(umsatz_total or 0))
    pax_total = Decimal(str(pax_total or 0))
    count = int(booking_count or 0)
    avg_booking = (umsatz_total / count).quantize(Decimal("0.01")) if count else None
    avg_pax_night = (umsatz_total / pax_total).quantize(Decimal("0.01")) if pax_total > 0 else None
    return AverageStats(count, umsatz_total, pax_total, avg_booking, avg_pax_night)
