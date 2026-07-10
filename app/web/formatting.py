"""Zahlen- und Zeitformatierung fuer die Templates (Schweizer Konvention: Apostroph als
Tausendertrennzeichen, Punkt als Dezimaltrennzeichen, z.B. 28'333.30)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

# Anzeige-Zeitzone: Bern/Zuerich (mit automatischer Sommer-/Winterzeit).
SWISS_TZ = ZoneInfo("Europe/Zurich")


def swissnum(value, decimals: int = 2) -> str:
    if value is None:
        return ""
    formatted = f"{Decimal(value):,.{decimals}f}"
    return formatted.replace(",", "'")


def swisstime(value: dt.datetime | None, fmt: str = "%d.%m.%Y %H:%M:%S") -> str:
    """Formatiert einen Zeitstempel in Berner Ortszeit (Europe/Zurich).

    In der DB werden Zeitstempel in UTC gespeichert. Kommt ein naiver Wert zurueck
    (z.B. bei SQLite ohne Zeitzoneninfo), wird er als UTC interpretiert."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(SWISS_TZ).strftime(fmt)
