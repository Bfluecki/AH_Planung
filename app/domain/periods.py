"""Ermittlung des Leistungszeitraums (service_start/service_end) je Buchung.

Offener Punkt lt. Konzept Abschnitt 9.2: Bexio liefert (Stand heutiger Beispielbelege)
keine strukturierten Datumsfelder auf Positionsebene fuer den Leistungszeitraum -
dieser steht nur als Freitext im Dokumenttitel (z.B. "Kurswoche du 25 au 28 mai 2026"
oder "Vereinsanlass vom 3. bis 5. Juli 2026").

Strategie:
1. Titel nach einem Datumsbereich durchsuchen (DE + FR Monatsnamen, sowie rein
   numerische Formen wie "25.-28.05.2026" oder "25.06.-03.07.2026").
2. Wird nichts gefunden, auf das Dokumentdatum als eintaegiges Ereignis zurueckfallen
   (typisch fuer Tagesanlaesse ohne Uebernachtung, siehe Konzept Abschnitt 9.6).

Sobald in Phase 1 klar ist, ob Bexio doch strukturierte Felder liefert, sollte diese
Funktion primaer jene Felder lesen und nur als Fallback auf den Titel-Parser zurueckgreifen.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

_MONTHS = {
    # Deutsch
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
    # Französisch
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai_fr": 5,
    "juin": 6, "juillet": 7, "aout": 8, "août": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
# "mai" ist in DE und FR identisch - kein eigener Eintrag noetig, oben entfernt Duplikat.
_MONTHS.pop("mai_fr", None)

_MONTH_NAMES_PATTERN = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))

# Trennwort/-zeichen zwischen zwei Tagesangaben. Reale Bexio-Titel verwenden nicht
# konsequent "bis" - haeufig auch "-", "–" oder "/" (z.B. "06./07. März 2027",
# "09.-10. Mai 2026"). "vom"/"vom...zum" ist ein optionales Praefix, kein Pflichtwort.
_SEP = r"(?:bis(?:\s+zum)?|[-–/])"
_VOM = r"(?:vom\s+)?"

# "21. bis 23. Mai 2027" / "vom 16.-17. April 2026" / "06./07. März 2027"
_DE_SAME_MONTH_RANGE_RE = re.compile(
    rf"{_VOM}(\d{{1,2}})\.?\s*{_SEP}\s*(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)
# "du 25 au 28 mai 2026"
_FR_RANGE_RE = re.compile(
    rf"du\s+(\d{{1,2}})\.?\s*au\s+(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)
# Bereich mit unterschiedlichen Monaten: "vom 25. Juni bis 3. Juli 2026" /
# "29. März bis 3. April 2027" / "31. Januar - 1. Februar 2026" (ohne "vom")
_DE_CROSS_MONTH_RANGE_RE = re.compile(
    rf"{_VOM}(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s*{_SEP}\s*(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)
# FR Bereich mit unterschiedlichen Monaten: "du 25 mai au 3 juin 2026"
_FR_RANGE_CROSS_MONTH_RE = re.compile(
    rf"du\s+(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+au\s+(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)
# Rein numerisch: "25.-28.05.2026" oder "25.06.-03.07.2026" oder "25.06.2026-03.07.2026"
_NUMERIC_RANGE_RE = re.compile(
    r"(\d{1,2})\.(?:(\d{1,2})\.)?(?:(\d{4})\.?)?\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})"
)
# Einzelnes Datum ohne Bereich: "Team Meeting 03. November 2026"
_DE_SINGLE_DATE_RE = re.compile(
    rf"(?:vom\s+|am\s+)?(\d{{1,2}})\.?\s*({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)
# Nur Monat/Jahr ohne Tagesangabe: "Probewochenende ... Januar 2027" -> ganzer Monat
_BARE_MONTH_RE = re.compile(
    rf"({_MONTH_NAMES_PATTERN})\s+(\d{{4}})",
    re.IGNORECASE,
)


def _month_num(name: str) -> int:
    return _MONTHS[name.lower()]


def extract_service_period(
    title: str, fallback_date: dt.date | None = None
) -> tuple[dt.date | None, dt.date | None]:
    """Gibt (service_start, service_end) zurueck, best-effort aus dem Titel geparst.

    Reihenfolge nach Praezision: numerischer Bereich > Bereich mit zwei Monatsnamen >
    Bereich mit einem Monatsnamen > Einzeldatum > nur Monat/Jahr (ganzer Monat) >
    Dokumentdatum als letzter Fallback."""
    if title:
        m = _NUMERIC_RANGE_RE.search(title)
        if m:
            d1, mon1, year1, d2, mon2, year2 = m.groups()
            try:
                end = dt.date(int(year2), int(mon2), int(d2))
                start_month = int(mon1) if mon1 else int(mon2)
                start_year = int(year1) if year1 else int(year2)
                start = dt.date(start_year, start_month, int(d1))
                return start, end
            except ValueError:
                pass

        for regex in (_DE_CROSS_MONTH_RANGE_RE, _FR_RANGE_CROSS_MONTH_RE):
            m = regex.search(title)
            if m:
                d1, mon1, d2, mon2, year = m.groups()
                try:
                    start = dt.date(int(year), _month_num(mon1), int(d1))
                    end = dt.date(int(year), _month_num(mon2), int(d2))
                    return start, end
                except ValueError:
                    pass

        for regex in (_DE_SAME_MONTH_RANGE_RE, _FR_RANGE_RE):
            m = regex.search(title)
            if m:
                d1, d2, mon, year = m.groups()
                try:
                    month = _month_num(mon)
                    start = dt.date(int(year), month, int(d1))
                    end = dt.date(int(year), month, int(d2))
                    return start, end
                except ValueError:
                    pass

        m = _DE_SINGLE_DATE_RE.search(title)
        if m:
            d, mon, year = m.groups()
            try:
                single = dt.date(int(year), _month_num(mon), int(d))
                return single, single
            except ValueError:
                pass

        m = _BARE_MONTH_RE.search(title)
        if m:
            mon, year = m.groups()
            try:
                month = _month_num(mon)
                start = dt.date(int(year), month, 1)
                end = dt.date(int(year), month, calendar.monthrange(int(year), month)[1])
                return start, end
            except ValueError:
                pass

    if fallback_date is not None:
        return fallback_date, fallback_date
    return None, None


def nights_between(start: dt.date, end: dt.date) -> int:
    """Anzahl Naechte eines Aufenthalts (end - start in Tagen, minimum 0)."""
    return max((end - start).days, 0)


def days_in_month_overlap(start: dt.date, end: dt.date, year: int, month: int) -> int:
    """Anzahl Kalendertage von [start, end] (inklusive), die in year-month liegen."""
    month_start = dt.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = dt.date(year, month, last_day)
    overlap_start = max(start, month_start)
    overlap_end = min(end, month_end)
    if overlap_start > overlap_end:
        return 0
    return (overlap_end - overlap_start).days + 1
