"""Gemeinsamer Kontext der Navigationsleiste.

Alle Seiten teilen sich dieselbe Leiste. Damit nicht jede Route dieselben Werte
zusammensuchen muss - und damit eine neue Seite nichts vergessen kann - liefert
nav_context() den vollstaendigen Satz. Seiten ohne Status-/Suchfilter setzen
mit_filter=False; die Leiste zeigt dort nur Bereiche, Planjahr und Verwaltung.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import ROLE_ADMIN, User
from app.config import get_settings
from app.models import Booking, MonthlyAllocation, Quote

# Reihenfolge der Status-Checkboxen in der Leiste.
ALL_STATUSES = ["nur_angebot", "beauftragt", "verrechnet", "storniert"]


def navigations_zaehler(db: Session, year: int) -> dict:
    """Kennzahlen neben den Bereichen - zeigen auf einen Blick, wo etwas liegt."""
    buchungen = (
        db.query(func.count(func.distinct(MonthlyAllocation.booking_id)))
        .filter(MonthlyAllocation.year == year)
        .scalar()
        or 0
    )
    anlaesse = (
        db.query(func.count(Booking.id))
        .filter(
            Booking.service_start.isnot(None),
            Booking.service_start <= dt.date(year, 12, 31),
            Booking.service_end >= dt.date(year, 1, 1),
        )
        .scalar()
        or 0
    )
    status_ids = get_settings().quote_declined_status_id_set
    abgelehnt = 0
    if status_ids:
        abgelehnt = (
            db.query(func.count(Quote.id))
            .filter(Quote.kb_item_status_id.in_(sorted(status_ids)))
            .filter(Quote.quote_date.isnot(None))
            .scalar()
            or 0
        )
    return {"buchungen": buchungen, "anlaesse": anlaesse, "abgelehnt": abgelehnt}


def nav_context(
    db: Session,
    user: User,
    year: int,
    aktiv: str,
    *,
    ziel_url: str = "",
    mit_jahr: bool = True,
    mit_filter: bool = False,
    selected_statuses: set[str] | None = None,
    default_statuses: set[str] | None = None,
    search: str = "",
) -> dict:
    """Alles, was _seitenleiste.html braucht - inklusive Konto und Zaehlern."""
    return {
        "aktiv": aktiv,
        "ziel_url": ziel_url,
        "mit_jahr": mit_jahr,
        "mit_filter": mit_filter,
        "year": year,
        "all_statuses": ALL_STATUSES,
        "selected_statuses": selected_statuses or set(),
        "default_statuses": default_statuses or set(),
        "search": search,
        "zaehler": navigations_zaehler(db, year),
        "current_user": user.username,
        "is_admin": user.role == ROLE_ADMIN,
    }
