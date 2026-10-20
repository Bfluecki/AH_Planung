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
import html
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.periods import extract_service_period
from app.models import Contact, Order, Quote

# Freitextfelder eines Bexio-Belegs, in denen der interne Kommentar stehen kann -
# der erste nicht leere Treffer gewinnt. Welches Feld Bexio tatsaechlich liefert,
# zeigt /admin/debug/angebot-status an den echten Daten; dort gefundene Namen hier
# ergaenzen.
#
# "footer"/"header" stehen bewusst NICHT in der Liste: das sind die gedruckten
# Texte des Belegs (Zuschlagstabelle, Grussformel) und damit per Definition kein
# interner Kommentar. Sie standen frueher drin und fuellten die Spalte
# "Begruendung" bei jedem Angebot mit derselben Standardfloskel - eine Begruendung,
# die keine war. Lieber leer als falsch.
COMMENT_FIELDS = (
    "internal_note",
    "internal_comment",
    "remarks",
    "comment",
    "note",
    "notes",
    "bemerkung",
    "bemerkungen",
)

# Bexio liefert Freitexte als HTML-Fragment ("Zuschl&auml;ge:<br />Einzelzimmer ...").
# Ungefiltert landet das Markup im Report und ist unlesbar; als HTML ausgeben waere
# eine Einladung, fremden Code in die Seite zu heben. Also: in Klartext wandeln.
_ZEILENUMBRUCH_RE = re.compile(r"<\s*br\s*/?\s*>|</\s*(?:p|div|li|tr)\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_LEERZEILEN_RE = re.compile(r"\n{3,}")


def html_zu_text(wert: str) -> str:
    """HTML-Fragment aus Bexio in lesbaren Klartext wandeln.

    Zeilenstruktur bleibt erhalten (<br> und Blockenden werden Umbrueche), alles
    andere Markup faellt weg, Entities werden aufgeloest ("&auml;" -> "ä").
    """
    if not wert:
        return ""
    text = _ZEILENUMBRUCH_RE.sub("\n", wert)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Zeilenweise trimmen, damit eingerücktes Markup keine Treppen hinterlaesst.
    zeilen = [zeile.strip() for zeile in text.replace("\r\n", "\n").split("\n")]
    return _LEERZEILEN_RE.sub("\n\n", "\n".join(zeilen)).strip()


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
    # Aus welchem Bexio-Feld die Begruendung stammt - macht eine Fehlzuordnung
    # sichtbar, statt sie als Begruendung auszugeben.
    begruendung_feld: str
    # Jahr, unter dem die Zeile gefuehrt wird, und woher es stammt ("anlass" aus
    # dem Leistungszeitraum, "angebotsdatum" als Notbehelf).
    planjahr: int | None
    planjahr_quelle: str
    status_id: int | None
    # True, wenn zum Angebot trotz Ablehnung ein Auftrag existiert - dann ist
    # entweder der Status in Bexio veraltet oder es wurde doch noch gebucht.
    hat_auftrag: bool


def extract_comment_mit_feld(*raws: dict | None) -> tuple[str, str]:
    """Erstes befuelltes Freitextfeld als Klartext, plus Name des Feldes.

    Der interne Kommentar wird je nach Arbeitsweise am Angebot oder am daraus
    entstandenen Auftrag erfasst, deshalb werden beide Belege durchsucht. Der
    Feldname wird mitgegeben, damit im Report sichtbar bleibt, woher der Text
    stammt - eine Fehlzuordnung faellt sonst nicht auf.
    """
    for raw in raws:
        if not isinstance(raw, dict):
            continue
        for field in COMMENT_FIELDS:
            text = html_zu_text(raw.get(field)) if isinstance(raw.get(field), str) else ""
            if text:
                return text, field
    return "", ""


def extract_comment(*raws: dict | None) -> str:
    """Nur der Text - siehe extract_comment_mit_feld()."""
    return extract_comment_mit_feld(*raws)[0]


def _planjahr(
    service_start: dt.date | None, service_end: dt.date | None, quote_date: dt.date | None
) -> tuple[int | None, str]:
    """Unter welchem Planjahr laeuft ein abgelehntes Angebot?

    Nach dem Anlass, nicht nach dem Angebotsdatum. Die ganze Planung ist nach dem
    Leistungszeitraum organisiert - ein im Mai 2026 abgelehntes Angebot fuer einen
    Anlass im Januar 2027 gehoert zu 2027. Nach dem Angebotsdatum zu filtern liess
    den Report leer erscheinen, waehrend im Dashboard desselben Jahres die
    abgelehnte Buchung stand.

    Steht im Titel kein Datum, bleibt das Angebotsdatum als Notbehelf.
    """
    if service_start is not None:
        return service_start.year, "anlass"
    if quote_date is not None:
        return quote_date.year, "angebotsdatum"
    return None, "unbekannt"


def _liegt_im_jahr(zeile: "DeclinedQuote", year: int) -> bool:
    """Mehrjaehrige Anlaesse zaehlen in jedem beruehrten Jahr."""
    if zeile.service_start is not None and zeile.service_end is not None:
        return zeile.service_start.year <= year <= zeile.service_end.year
    return zeile.planjahr == year


def declined_quote_count(db: Session, status_ids: set[int], year: int | None = None) -> int:
    """Anzahl abgelehnter Angebote - dieselbe Menge, die declined_quotes liefert."""
    return len(declined_quotes(db, status_ids, year))


def declined_quotes(
    db: Session, status_ids: set[int], year: int | None = None
) -> list[DeclinedQuote]:
    """Alle abgelehnten Angebote, neueste zuerst.

    ``year`` filtert auf das Planjahr des Anlasses (siehe _planjahr) - wie die
    uebrige Planung. Steht im Titel kein Datum, greift das Angebotsdatum.
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
        start, end = extract_service_period(quote.title, None)
        contact = contacts.get(quote.contact_id) if quote.contact_id else None
        order = orders_by_quote.get(quote.id)
        begruendung, feld = extract_comment_mit_feld(
            quote.raw, order.raw if order else None
        )
        planjahr, quelle = _planjahr(start, end, quote.quote_date)
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
                begruendung=begruendung,
                begruendung_feld=feld,
                planjahr=planjahr,
                planjahr_quelle=quelle,
                status_id=quote.kb_item_status_id,
                hat_auftrag=order is not None,
            )
        )
    if year is not None:
        rows = [r for r in rows if _liegt_im_jahr(r, year)]
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
