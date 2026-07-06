"""Periodengerechte Verteilung von Umsatz/Naechten/PAX auf Leistungsmonate.

Siehe Konzept Abschnitt 3 ("Abgrenzungslogik"). Zwei Modi:

- "full_month": die ganze Buchung wird dem Monat von service_start zugeordnet.
- "prorata" (Default/empfohlen): Umsatz und Naechte werden auf Basis der
  tatsaechlichen Naechte je Kalendermonat verteilt. Reine Tagesanlaesse ohne
  Uebernachtung (Konzept 9.6) werden stattdessen ueber die Kalendertage verteilt.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from app.domain.periods import days_in_month_overlap

ALLOCATION_MODE_FULL_MONTH = "full_month"
ALLOCATION_MODE_PRORATA = "prorata"


@dataclass
class AllocationInput:
    service_start: dt.date
    service_end: dt.date
    revenue: Decimal
    pax: int | None = None
    # Gesamtzahl Naechte des Aufenthalts (aus Positionsmengen, z.B. "59.00 Uebernachtungen").
    # None/0 => Tagesanlass ohne Uebernachtung, es wird ueber Tage statt Naechte verteilt.
    nights: Decimal | None = None


@dataclass
class MonthShare:
    year: int
    month: int
    nights: Decimal
    days: Decimal
    pax_nights: Decimal
    revenue: Decimal


def _month_sequence(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    cursor = dt.date(start.year, start.month, 1)
    end_marker = dt.date(end.year, end.month, 1)
    while cursor <= end_marker:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return months


def allocate(inp: AllocationInput, mode: str = ALLOCATION_MODE_PRORATA) -> list[MonthShare]:
    """Verteilt eine Buchung auf ihre Leistungsmonate. Gibt eine oder mehrere MonthShares zurueck."""
    start, end = inp.service_start, inp.service_end
    if start is None or end is None:
        return []
    if end < start:
        start, end = end, start

    if mode == ALLOCATION_MODE_FULL_MONTH:
        pax_nights = Decimal(inp.pax or 0) * (inp.nights or Decimal("0"))
        return [
            MonthShare(
                year=start.year,
                month=start.month,
                nights=inp.nights or Decimal("0"),
                days=Decimal((end - start).days + 1),
                pax_nights=pax_nights,
                revenue=inp.revenue,
            )
        ]

    has_nights = inp.nights is not None and inp.nights > 0
    months = _month_sequence(start, end)
    if not months:
        return []

    if has_nights:
        # Naechte werden dem Datum ihres Beginns zugeordnet: Nacht 1 = [start, start+1), etc.
        # Damit koennen wir die Gesamt-Naechte (aus Positionsmengen) proportional zur
        # rechnerischen Aufenthaltsdauer auf die ueberlappenden Monate aufteilen.
        stay_nights = max((end - start).days, 1)
        shares: list[MonthShare] = []
        total_weight = Decimal("0")
        weights: list[Decimal] = []
        for year, month in months:
            # Anzahl Naechte, deren Startdatum in diesem Monat liegt.
            night_dates_in_month = 0
            cursor = start
            for _ in range(stay_nights):
                if cursor.year == year and cursor.month == month:
                    night_dates_in_month += 1
                cursor += dt.timedelta(days=1)
            weight = Decimal(night_dates_in_month)
            weights.append(weight)
            total_weight += weight

        if total_weight == 0:
            total_weight = Decimal(1)
            weights = [Decimal(1)] + [Decimal(0)] * (len(months) - 1)

        allocated_revenue_sum = Decimal("0")
        allocated_nights_sum = Decimal("0")
        for idx, (year, month) in enumerate(months):
            weight = weights[idx]
            fraction = weight / total_weight
            month_nights = (inp.nights * fraction).quantize(Decimal("0.01"))
            month_revenue = (inp.revenue * fraction).quantize(Decimal("0.01"))
            allocated_nights_sum += month_nights
            allocated_revenue_sum += month_revenue
            days = Decimal(days_in_month_overlap(start, end, year, month))
            pax_nights = Decimal(inp.pax or 0) * month_nights
            shares.append(
                MonthShare(
                    year=year, month=month, nights=month_nights, days=days,
                    pax_nights=pax_nights, revenue=month_revenue,
                )
            )
        # Rundungsdifferenz dem letzten Monat zuschlagen, damit Summe exakt stimmt.
        if shares:
            shares[-1].nights += inp.nights - allocated_nights_sum
            shares[-1].revenue += inp.revenue - allocated_revenue_sum
        return shares

    # Tagesanlass ohne Uebernachtung: Verteilung ueber Kalendertage (Konzept 9.6).
    total_days = (end - start).days + 1
    shares = []
    allocated_revenue_sum = Decimal("0")
    for year, month in months:
        days = days_in_month_overlap(start, end, year, month)
        fraction = Decimal(days) / Decimal(total_days)
        month_revenue = (inp.revenue * fraction).quantize(Decimal("0.01"))
        allocated_revenue_sum += month_revenue
        shares.append(
            MonthShare(
                year=year, month=month, nights=Decimal("0"), days=Decimal(days),
                pax_nights=Decimal("0"), revenue=month_revenue,
            )
        )
    if shares:
        shares[-1].revenue += inp.revenue - allocated_revenue_sum
    return shares
