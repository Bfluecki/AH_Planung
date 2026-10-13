"""Kalenderansicht auf Wochenbasis (Konzept: Belegungsplanung statt Zahlenkolonne).

Zeigt die Anlaesse eines Jahres als Balken ueber die Kalenderwochen. Grundlage sind
service_start/service_end der Buchung - also der Leistungszeitraum, nicht das
Dokumentdatum. Erstreckt sich ein Anlass ueber mehrere Wochen, wird er je Woche als
eigener Balken ausgegeben und an den Schnittstellen als "laeuft weiter" markiert.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Booking

WEEKDAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONTH_NAMES = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


@dataclass
class CalendarEntry:
    """Ein Anlass innerhalb einer Woche - als Balken ueber die betroffenen Tage."""

    booking_key: str
    kunde: str
    anlass: str
    status: str
    service_start: dt.date
    service_end: dt.date
    umsatz_soll: Decimal
    zeitraum_unsicher: bool
    # Spalte 1-7 (Mo-So) und Anzahl Tage, die der Balken in dieser Woche belegt.
    start_column: int
    span: int
    laeuft_vorher: bool
    laeuft_nachher: bool

    @property
    def end_column(self) -> int:
        return self.start_column + self.span - 1


@dataclass
class CalendarWeek:
    week: int
    monday: dt.date
    sunday: dt.date
    entries: list[CalendarEntry] = field(default_factory=list)
    # Monatsname, wenn mit dieser Woche ein neuer Monat beginnt - dient als
    # Orientierungspunkt im Jahresraster von gut 52 Zeilen.
    neuer_monat: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.entries

    @property
    def label(self) -> str:
        if self.monday.month == self.sunday.month:
            return f"{self.monday.day}.–{self.sunday.strftime('%d.%m.')}"
        return f"{self.monday.strftime('%d.%m.')}–{self.sunday.strftime('%d.%m.')}"


def _weeks_of_year(year: int) -> list[tuple[dt.date, dt.date]]:
    """Alle Montag-Sonntag-Paare, die das Kalenderjahr beruehren."""
    monday = dt.date(year, 1, 1) - dt.timedelta(days=dt.date(year, 1, 1).weekday())
    last = dt.date(year, 12, 31)
    wochen = []
    while monday <= last:
        wochen.append((monday, monday + dt.timedelta(days=6)))
        monday += dt.timedelta(days=7)
    return wochen


def calendar_weeks(
    db: Session, year: int, statuses: set[str], search: str | None = None
) -> list[CalendarWeek]:
    """Wochenweise Belegung des Jahres, gefiltert nach Status und Suchbegriff."""
    query = db.query(Booking).filter(
        Booking.service_start.isnot(None), Booking.service_end.isnot(None)
    )
    if statuses:
        query = query.filter(Booking.status.in_(sorted(statuses)))
    bookings = query.all()

    if search:
        needle = search.lower()
        bookings = [
            b for b in bookings if needle in (b.kunde or "").lower() or needle in (b.anlass or "").lower()
        ]

    wochen = []
    letzter_monat = None
    for monday, sunday in _weeks_of_year(year):
        woche = CalendarWeek(week=monday.isocalendar()[1], monday=monday, sunday=sunday)
        if monday.month != letzter_monat:
            name = MONTH_NAMES[monday.month - 1]
            # KW 1 kann im Vorjahr beginnen - Jahr dazuschreiben, sonst steht ueber
            # dem Kalender 2026 unvermittelt "Dezember".
            woche.neuer_monat = name if monday.year == year else f"{name} {monday.year}"
            letzter_monat = monday.month
        for booking in bookings:
            start, end = booking.service_start, booking.service_end
            if end < monday or start > sunday:
                continue
            sichtbar_ab = max(start, monday)
            sichtbar_bis = min(end, sunday)
            woche.entries.append(
                CalendarEntry(
                    booking_key=booking.booking_key,
                    kunde=booking.kunde,
                    anlass=booking.anlass,
                    status=booking.status,
                    service_start=start,
                    service_end=end,
                    umsatz_soll=booking.umsatz_soll or Decimal("0"),
                    zeitraum_unsicher=booking.zeitraum_unsicher,
                    start_column=sichtbar_ab.weekday() + 1,
                    span=(sichtbar_bis - sichtbar_ab).days + 1,
                    laeuft_vorher=start < monday,
                    laeuft_nachher=end > sunday,
                )
            )
        # Innerhalb der Woche nach Starttag, dann laengster Balken zuerst - so
        # entsteht ein ruhiges Bild statt wild verschachtelter Zeilen.
        woche.entries.sort(key=lambda e: (e.start_column, -e.span, e.anlass))
        wochen.append(woche)
    return wochen


def calendar_summary(wochen: list[CalendarWeek]) -> dict:
    """Kennzahlen ueber die dargestellten Wochen - je Buchung nur einmal gezaehlt."""
    gesehen: set[str] = set()
    je_status: dict[str, int] = {}
    umsatz = Decimal("0")
    belegte_wochen = 0
    for woche in wochen:
        if woche.entries:
            belegte_wochen += 1
        for eintrag in woche.entries:
            if eintrag.booking_key in gesehen:
                continue
            gesehen.add(eintrag.booking_key)
            je_status[eintrag.status] = je_status.get(eintrag.status, 0) + 1
            umsatz += eintrag.umsatz_soll
    return {
        "anlaesse": len(gesehen),
        "je_status": je_status,
        "umsatz_soll": umsatz,
        "belegte_wochen": belegte_wochen,
    }
