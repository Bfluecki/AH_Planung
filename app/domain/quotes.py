"""Abgelehnte Angebote (kb_offer) - Report mit der in Bexio erfassten Begruendung.

Bexio fuehrt den Zustand eines Angebots in ``kb_item_status_id``. Welche Zahl dort
"abgelehnt" bedeutet, ist je Mandant gegen die echten Daten zu pruefen - deshalb ist
die Zuordnung ueber die Env-Variable QUOTE_DECLINED_STATUS_IDS konfigurierbar und
nicht fest verdrahtet. Welche Status tatsaechlich vorkommen, zeigt
/admin/debug/angebot-status.

Die Begruendung schreibt Bexio nicht in ein eigenes Feld, sondern in eines der
Freitextfelder des Belegs. Deshalb werden mehrere Kandidaten der Reihe nach
geprueft (gleiches Muster wie app/sync/service.py::_pick).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.periods import extract_service_period
from app.models import Contact, Order, Quote

# Freitextfelder eines Bexio-Belegs, in denen der interne Kommentar stehen kann -
# der erste nicht leere Treffer gewinnt. Welches Feld Bexio tatsaechlich liefert,
# zeigt /admin/debug/angebot-status an den echten Daten; dort gefundene Namen hier
# ergaenzen.
COMMENT_FIELDS = (
    "internal_note",
    "internal_comment",
    "remarks",
    "comment",
    "note",
    "notes",
    "bemerkung",
    "bemerkungen",
    "footer",
    "header",
)


@dataclass
class DeclinedQuote:
    """Eine Zeile des Reports "Abgelehnte Angebote"."""

    quote_id: int
    document_nr: str
    quote_date: dt.date | None
    valid_until: dt.date | None
    kunde: str
    anlass: str
    service_start: dt.date | None
    service_end: dt.date | None
    total: Decimal
    begruendung: str
    status_id: int | None
    # True, wenn zum Angebot trotz Ablehnung ein Auftrag existiert - dann ist
    # entweder der Status in Bexio veraltet oder es wurde doch noch gebucht.
    hat_auftrag: bool


def extract_comment(*raws: dict | None) -> str:
    """Erstes befuelltes Freitextfeld - der Reihe nach ueber mehrere Belege.

    Der interne Kommentar wird je nach Arbeitsweise am Angebot oder am daraus
    entstandenen Auftrag erfasst, deshalb werden beide Belege durchsucht.
    """
    for raw in raws:
        if not isinstance(raw, dict):
            continue
        for field in COMMENT_FIELDS:
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def declined_quotes(
    db: Session, status_ids: set[int], year: int | None = None
) -> list[DeclinedQuote]:
    """Alle abgelehnten Angebote, neueste zuerst.

    ``year`` filtert auf das Jahr des Angebotsdatums (nicht des Anlasses) - das ist
    das einzige Datum, das bei jedem Angebot sicher vorhanden ist. Der Zeitraum des
    Anlasses wird zusaetzlich ausgewiesen, soweit er aus dem Titel hervorgeht.
    """
    if not status_ids:
        return []

    quotes = (
        db.query(Quote)
        .filter(Quote.kb_item_status_id.in_(sorted(status_ids)))
        .order_by(Quote.quote_date.desc().nullslast(), Quote.id.desc())
        .all()
    )
    contacts = {c.id: c for c in db.query(Contact).all()}
    # Auftrag je Angebot: liefert zugleich den Beleg, an dem der interne Kommentar
    # haengen kann, wenn er nicht am Angebot selbst erfasst wurde.
    orders_by_quote = {
        o.source_quote_bexio_id: o
        for o in db.query(Order).filter(Order.source_quote_bexio_id.isnot(None)).all()
    }

    rows: list[DeclinedQuote] = []
    for quote in quotes:
        if year is not None and (quote.quote_date is None or quote.quote_date.year != year):
            continue
        start, end = extract_service_period(quote.title, None)
        contact = contacts.get(quote.contact_id) if quote.contact_id else None
        order = orders_by_quote.get(quote.id)
        rows.append(
            DeclinedQuote(
                quote_id=quote.id,
                document_nr=quote.document_nr,
                quote_date=quote.quote_date,
                valid_until=quote.valid_until,
                kunde=contact.name if contact else "",
                anlass=quote.title,
                service_start=start,
                service_end=end,
                total=quote.total or Decimal("0"),
                begruendung=extract_comment(quote.raw, order.raw if order else None),
                status_id=quote.kb_item_status_id,
                hat_auftrag=order is not None,
            )
        )
    return rows


def quote_status_overview(db: Session) -> list[dict]:
    """Welche Angebots-Status kommen in den Daten vor, wie oft, mit Beispielen?

    Grundlage, um QUOTE_DECLINED_STATUS_IDS richtig zu setzen: der Mandant erkennt
    seine eigenen Angebote an Nummer und Titel wieder.
    """
    overview: dict[int | None, dict] = {}
    for quote in db.query(Quote).all():
        entry = overview.setdefault(
            quote.kb_item_status_id,
            {"status_id": quote.kb_item_status_id, "anzahl": 0, "summe": Decimal("0"), "beispiele": []},
        )
        entry["anzahl"] += 1
        entry["summe"] += quote.total or Decimal("0")
        if len(entry["beispiele"]) < 3:
            entry["beispiele"].append(
                {
                    "document_nr": quote.document_nr,
                    "titel": quote.title,
                    "datum": quote.quote_date.isoformat() if quote.quote_date else None,
                    "freitextfelder": {
                        field: quote.raw.get(field)
                        for field in COMMENT_FIELDS
                        if isinstance(quote.raw, dict) and quote.raw.get(field)
                    },
                }
            )
    return sorted(overview.values(), key=lambda e: (e["status_id"] is None, e["status_id"] or 0))
