"""Periodengerechte Verteilung von Umsatz/Naechten/PAX-Naechten auf Leistungsmonate.

Siehe Konzept Abschnitt 3 ("Abgrenzungslogik"). Zwei Modi:

- "full_month": die ganze Buchung wird dem Monat von service_start zugeordnet.
- "prorata" (Default/empfohlen): Umsatz und PAX-Naechte werden auf Basis der
  tatsaechlichen Naechte je Kalendermonat verteilt. Reine Tagesanlaesse ohne
  Uebernachtung (Konzept 9.6) werden stattdessen ueber die Kalendertage verteilt.

Wichtige Unterscheidung (siehe auch Konzept Abschnitt 3, "PAX-Naechte"):
- "Naechte" = physische Aufenthaltsdauer (Kalendernaechte), unabhaengig von der
  Personenzahl - z.B. 4 Naechte, egal ob 1 oder 40 Personen da waren.
- "PAX-Naechte" = die von Bexio gelieferte Positionsmenge (Produktcode LH-UEB etc.,
  "pro Person und Nacht"), also bereits Personen x Naechte, z.B. 16 fuer 4 Personen
  ueber 4 Naechte.
Beide werden getrennt gefuehrt; PAX-Naechte werden NICHT nochmals mit PAX
multipliziert (das waere PAX², ein frueherer Bug).
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
    # PAX-Naechte = Bexio-Positionsmenge fuer Uebernachtungsprodukte (bereits
    # Personen x Naechte, z.B. "16.00 Uebernachtungen"). None/0 => Tagesanlass ohne
    # Uebernachtung, es wird ueber Tage statt Naechte verteilt.
    pax_nights: Decimal | None = None


@dataclass
class MonthShare:
    year: int
    month: int
    nights: Decimal  # physische Naechte in diesem Monat (unabhaengig von PAX)
    days: Decimal
    pax_nights: Decimal  # anteilige PAX-Naechte in diesem Monat
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

    stay_nights = max((end - start).days, 0)

    if mode == ALLOCATION_MODE_FULL_MONTH:
        return [
            MonthShare(
                year=start.year,
                month=start.month,
                nights=Decimal(stay_nights),
                days=Decimal((end - start).days + 1),
                pax_nights=inp.pax_nights or Decimal("0"),
                revenue=inp.revenue,
            )
        ]

    has_nights = inp.pax_nights is not None and inp.pax_nights > 0
    months = _month_sequence(start, end)
    if not months:
        return []

    if has_nights:
        # Naechte werden dem Datum ihres Beginns zugeordnet: Nacht 1 = [start, start+1), etc.
        # Die physische Naechte-Anzahl je Monat dient als Gewicht, um sowohl die
        # PAX-Naechte als auch den Umsatz proportional auf die ueberlappenden Monate
        # zu verteilen (Kalender-Gewichtung ist unabhaengig davon, ob man PAX-Naechte
        # oder physische Naechte verteilt - die Anteile pro Monat sind identisch).
        effective_stay_nights = max(stay_nights, 1)
        shares: list[MonthShare] = []
        total_weight = 0
        weights: list[int] = []
        for year, month in months:
            night_dates_in_month = 0
            cursor = start
            for _ in range(effective_stay_nights):
                if cursor.year == year and cursor.month == month:
                    night_dates_in_month += 1
                cursor += dt.timedelta(days=1)
            weights.append(night_dates_in_month)
            total_weight += night_dates_in_month

        if total_weight == 0:
            total_weight = 1
            weights = [1] + [0] * (len(months) - 1)

        allocated_revenue_sum = Decimal("0")
        allocated_pax_nights_sum = Decimal("0")
        for idx, (year, month) in enumerate(months):
            weight = weights[idx]
            fraction = Decimal(weight) / Decimal(total_weight)
            month_pax_nights = (inp.pax_nights * fraction).quantize(Decimal("0.01"))
            month_revenue = (inp.revenue * fraction).quantize(Decimal("0.01"))
            allocated_pax_nights_sum += month_pax_nights
            allocated_revenue_sum += month_revenue
            days = Decimal(days_in_month_overlap(start, end, year, month))
            shares.append(
                MonthShare(
                    year=year, month=month, nights=Decimal(weight), days=days,
                    pax_nights=month_pax_nights, revenue=month_revenue,
                )
            )
        # Rundungsdifferenz dem letzten Monat zuschlagen, damit Summe exakt stimmt.
        if shares:
            shares[-1].pax_nights += inp.pax_nights - allocated_pax_nights_sum
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
