"""Baut die Ausgabestruktur (Detailzeilen je Monat + Jahresuebersicht) aus der DB.

Gemeinsame Grundlage fuer JSON-API, Excel-Export und Web-Dashboard (Konzept
Abschnitt 5, "Format: ... alle drei aus derselben Datenbasis").
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.budget import get_budget_overrides
from app.config import Settings, get_settings
from app.domain.aggregation import MonthlyFigure, YearSummary, aggregate_year
from app.models import Booking, MonthlyAllocation

_MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


@dataclass
class ReportRow:
    booking_key: str
    kunde: str
    anlass: str
    zeitraum: str
    status: str
    linked_by: str
    pax_soll: int | None
    pax_ist: int | None
    tage: Decimal
    naechte: Decimal
    pax_naechte: Decimal
    umsatz_soll: Decimal
    umsatz_ist: Decimal
    rechnungsmonat: str
    zahlungsmonat: str
    kommentar: str = ""

    @property
    def pax_diff(self) -> int | None:
        if self.pax_soll is None or self.pax_ist is None:
            return None
        return self.pax_ist - self.pax_soll

    @property
    def umsatz_diff(self) -> Decimal:
        return self.umsatz_ist - self.umsatz_soll


def format_zeitraum(start: dt.date | None, end: dt.date | None) -> str:
    if start is None:
        return ""
    if end is None or start == end:
        return start.strftime("%d.%m.%Y")
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%d.')}–{end.strftime('%d.%m.%Y')}"
    return f"{start.strftime('%d.%m.')}–{end.strftime('%d.%m.%Y')}"


def format_month(d: dt.date | None) -> str:
    if d is None:
        return ""
    return f"{_MONTH_NAMES_DE[d.month - 1]} {d.year}"


def build_report(
    db: Session,
    year: int,
    settings: Settings | None = None,
    statuses: set[str] | None = None,
) -> tuple[YearSummary, dict[int, list[ReportRow]]]:
    """statuses filtert nach Booking.status (z.B. nur "verrechnet"); None/leer = alle.
    Wirkt auf Jahresuebersicht UND Detailzeilen gleichermassen - beide werden aus
    derselben gefilterten Abfrage gebaut, es gibt keine separate "Gesamtbild"-Sicht."""
    settings = settings or get_settings()
    effective = get_effective_config(db, settings)

    query = (
        db.query(MonthlyAllocation)
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year)
    )
    if statuses:
        query = query.filter(Booking.status.in_(statuses))
    allocations = query.all()

    figures = [
        MonthlyFigure(
            year=a.year,
            month=a.month,
            nights_soll=a.nights_soll,
            nights_ist=a.nights_ist,
            days=a.days,
            umsatz_soll=a.umsatz_soll,
            umsatz_ist=a.umsatz_ist,
        )
        for a in allocations
    ]
    budget_overrides = get_budget_overrides(db, year)
    year_summary = aggregate_year(figures, year, effective.monthly_budget_chf, budget_overrides=budget_overrides)

    rows_by_month: dict[int, list[ReportRow]] = {m: [] for m in range(1, 13)}
    for a in allocations:
        booking: Booking = a.booking
        rows_by_month[a.month].append(
            ReportRow(
                booking_key=booking.booking_key,
                kunde=booking.kunde,
                anlass=booking.anlass,
                zeitraum=format_zeitraum(booking.service_start, booking.service_end),
                status=booking.status,
                linked_by=booking.linked_by,
                pax_soll=booking.pax_soll,
                pax_ist=booking.pax_ist,
                tage=a.days,
                naechte=a.nights_ist or a.nights_soll,
                pax_naechte=a.pax_nights_ist or a.pax_nights_soll,
                umsatz_soll=a.umsatz_soll,
                umsatz_ist=a.umsatz_ist,
                rechnungsmonat=format_month(booking.invoice_date),
                zahlungsmonat=format_month(booking.payment_date),
                kommentar=booking.comment,
            )
        )
    for month_rows in rows_by_month.values():
        month_rows.sort(key=lambda r: r.zeitraum)

    return year_summary, rows_by_month
