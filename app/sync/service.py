"""Sync-Service: Bexio -> PostgreSQL-Cache -> Transformationspipeline -> Bookings.

Feldnamen aus den Bexio-Rohobjekten sind ueber `_pick()` mit mehreren Kandidaten
abgesichert, weil die exakten Feldnamen laut Konzept Abschnitt 9.1/9.2 erst gegen
die echte API verifiziert werden koennen. Das komplette Rohobjekt wird zusaetzlich
in der `raw`-Spalte gespeichert, sodass nichts verloren geht, falls ein Feld hier
(noch) nicht gemappt ist.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.admin_config import get_effective_config
from app.bexio.client import BexioApiError, BexioClient
from app.config import Settings, get_settings
from app.domain.allocation import AllocationInput, allocate
from app.domain.periods import extract_service_period, nights_between
from app.domain.product_mapping import ist_uebernachtung
from app.domain.soll_ist import DocumentMetrics, compute_booking_metrics
from app.matching.chain import (
    BookingChain,
    CreditNoteRef,
    InvoiceRef,
    OrderRef,
    QuoteRef,
    build_booking_chains,
)
from app.models import (
    Booking,
    Contact,
    CreditNote,
    Invoice,
    LineItem,
    MonthlyAllocation,
    Order,
    Quote,
)


logger = logging.getLogger(__name__)

# Kandidaten-Feldnamen je logischem Wert - erste vorhandene wird verwendet.
# Bei Phase-1-Verifikation gegen die echte API hier ergaenzen/korrigieren.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "document_nr": ("document_nr", "nr", "kb_nr"),
    "title": ("title", "header", "name"),
    "contact_id": ("contact_id",),
    "date": ("date", "is_valid_from", "created_at"),
    "due_or_valid_until": ("valid_until", "is_valid_until", "is_valid_to", "due_date"),
    # Bexio liefert total_gross/total_net/total_taxes getrennt (verifiziert gegen
    # echte kb_invoice-Antworten, Konzept 9.1); "total" ist dort identisch zu
    # total_gross. Umsatz soll ohne MwSt ausgewiesen werden -> total_net zuerst.
    "total": ("total_net", "total", "total_gross"),
    "source_quote_id": ("kb_offer_id", "quote_id", "copied_from_offer_id"),
    "source_order_id": ("kb_order_id", "order_id", "copied_from_order_id"),
    "reference_invoice_id": ("kb_invoice_id", "invoice_id", "reference_id"),
    "payment_date": ("payment_date", "valuta_date", "paid_at"),
    "pax": ("pax", "anzahl_personen", "number_of_participants"),
    "product_code": ("intern_code", "code", "article_code"),
    "quantity": ("amount", "quantity"),
    "unit_price": ("unit_price",),
    # Bexio's tatsaechliches Feld fuer den Positions-Gesamtbetrag ist "position_total"
    # (verifiziert gegen echte kb_position_article-Antworten, Konzept 9.1).
    "position_total": ("position_total", "total", "amount_net"),
}

# Bexio traegt bei kb_position_article keinen eigenen Produktcode-Feld - der Code
# steht als Freitext im (HTML-)Beschreibungsfeld "text", z.B.
# "<strong>Fruehstueck</strong><br />Produktcode: AH-FRU<br />..." (verifiziert
# gegen echte Positionsdaten, Konzept 9.1). _pick() bleibt als Fallback bestehen,
# falls ein anderer Positionstyp den Code doch als eigenes Feld liefert.
# Faengt bis zum naechsten HTML-Tag/Zeilenumbruch ein, nicht nur [A-Za-z0-9-]:
# einige echte Codes enthalten Punkte, Leerzeichen oder ein "+" (z.B. "0.5 PN",
# "AH-SEM-PAU+"), siehe Produktkatalog-Export.
_PRODUCT_CODE_IN_TEXT_RE = re.compile(r"Produktcode:\s*([^<\r\n]+)", re.IGNORECASE)


def _extract_product_code(raw: dict) -> str | None:
    text = raw.get("text") or ""
    m = _PRODUCT_CODE_IN_TEXT_RE.search(text)
    if m:
        # HTML-Entities auflösen (z.B. "&uuml;" -> "ü") und Nicht-Break-Space (aus
        # "&nbsp;") mitentfernen - sonst landen kaputte Pseudo-Codes wie "AH-K&uuml;che"
        # oder ein blankes "&nbsp;" als eigene "unbekannte" Produktcodes im Report.
        code = html.unescape(m.group(1)).strip()
        if code:
            return code
    return _pick(raw, "product_code")


def _pick(raw: dict, key: str, default=None):
    for candidate in _FIELD_CANDIDATES[key]:
        if candidate in raw and raw[candidate] not in (None, ""):
            return raw[candidate]
    return default


def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return default


def _to_date(value) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    text = str(value)[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Rohdaten-Sync (Konzept Abschnitt 6, "Sync-Scheduler")
# ----------------------------------------------------------------------
def _upsert_contact(db: Session, raw: dict) -> Contact:
    contact = db.get(Contact, raw["id"])
    if contact is None:
        contact = Contact(id=raw["id"])
        db.add(contact)
    contact.contact_nr = str(raw.get("nr") or raw.get("contact_nr") or "")
    name_parts = [raw.get("name_1", ""), raw.get("name_2", "")]
    contact.name = " ".join(p for p in name_parts if p) or raw.get("name", "")
    contact.raw = raw
    contact.synced_at = dt.datetime.now(dt.timezone.utc)
    return contact


def sync_contacts(client: BexioClient, db: Session) -> int:
    count = 0
    for raw in client.list_contacts():
        _upsert_contact(db, raw)
        count += 1
    db.commit()
    return count


def _ensure_contact_exists(
    client: BexioClient, db: Session, contact_id: int | None, missing_contact_ids: set[int]
) -> int | None:
    """Stellt sicher, dass ein von einem Dokument referenzierter Kontakt lokal existiert,
    bevor er als contact_id (FK) gesetzt wird. Bexio's Listen-Endpunkt /2.0/contact
    liefert nicht zwingend archivierte/geloeschte Kontakte zurueck, die aber noch von
    einem alten Auftrag/einer Rechnung referenziert werden koennen - solche IDs werden
    hier gezielt einzeln nachgeladen. Existiert der Kontakt tatsaechlich nicht mehr,
    wird die Referenz auf None gesetzt statt die ganze Sync mit einer
    ForeignKeyViolation abzubrechen."""
    if contact_id is None:
        return None
    if contact_id in missing_contact_ids:
        return None
    if db.get(Contact, contact_id) is not None:
        return contact_id

    raw = client.get_contact(contact_id)
    if raw is None:
        logger.warning(
            "Kontakt %s wird von einem Dokument referenziert, existiert aber nicht "
            "(mehr) in Bexio - Referenz wird auf NULL gesetzt.", contact_id
        )
        missing_contact_ids.add(contact_id)
        return None

    _upsert_contact(db, raw)
    db.flush()  # Kontakt muss vor dem Dokument-Insert/Commit in der DB sichtbar sein
    return contact_id


def sync_quotes(client: BexioClient, db: Session, missing_contact_ids: set[int]) -> int:
    count = 0
    for raw in client.list_quotes():
        row = db.get(Quote, raw["id"])
        if row is None:
            row = Quote(id=raw["id"])
            db.add(row)
        row.document_nr = str(_pick(raw, "document_nr", ""))
        row.contact_id = _ensure_contact_exists(client, db, _pick(raw, "contact_id"), missing_contact_ids)
        row.title = _pick(raw, "title", "")
        row.quote_date = _to_date(_pick(raw, "date"))
        row.valid_until = _to_date(_pick(raw, "due_or_valid_until"))
        row.total = _to_decimal(_pick(raw, "total"))
        row.kb_item_status_id = raw.get("kb_item_status_id")
        row.raw = raw
        row.synced_at = dt.datetime.now(dt.timezone.utc)
        count += 1
    db.commit()
    return count


def sync_orders(client: BexioClient, db: Session, missing_contact_ids: set[int]) -> int:
    count = 0
    for raw in client.list_orders():
        row = db.get(Order, raw["id"])
        if row is None:
            row = Order(id=raw["id"])
            db.add(row)
        row.document_nr = str(_pick(raw, "document_nr", ""))
        row.contact_id = _ensure_contact_exists(client, db, _pick(raw, "contact_id"), missing_contact_ids)
        row.title = _pick(raw, "title", "")
        row.order_date = _to_date(_pick(raw, "date"))
        row.total = _to_decimal(_pick(raw, "total"))
        row.source_quote_bexio_id = _pick(raw, "source_quote_id")
        row.raw = raw
        row.synced_at = dt.datetime.now(dt.timezone.utc)
        count += 1
    db.commit()
    return count


def sync_invoices(client: BexioClient, db: Session, missing_contact_ids: set[int]) -> int:
    count = 0
    for raw in client.list_invoices():
        row = db.get(Invoice, raw["id"])
        if row is None:
            row = Invoice(id=raw["id"])
            db.add(row)
        row.document_nr = str(_pick(raw, "document_nr", ""))
        row.contact_id = _ensure_contact_exists(client, db, _pick(raw, "contact_id"), missing_contact_ids)
        row.title = _pick(raw, "title", "")
        row.invoice_date = _to_date(_pick(raw, "date"))
        row.payment_date = _to_date(_pick(raw, "payment_date"))
        row.total = _to_decimal(_pick(raw, "total"))
        row.source_order_bexio_id = _pick(raw, "source_order_id")
        row.raw = raw
        row.synced_at = dt.datetime.now(dt.timezone.utc)
        count += 1
    db.commit()
    return count


def sync_credit_notes(client: BexioClient, db: Session, missing_contact_ids: set[int]) -> int:
    count = 0
    try:
        for raw in client.list_credit_notes():
            row = db.get(CreditNote, raw["id"])
            if row is None:
                row = CreditNote(id=raw["id"])
                db.add(row)
            row.document_nr = str(_pick(raw, "document_nr", ""))
            row.contact_id = _ensure_contact_exists(client, db, _pick(raw, "contact_id"), missing_contact_ids)
            row.title = _pick(raw, "title", "")
            row.credit_note_date = _to_date(_pick(raw, "date"))
            row.total = _to_decimal(_pick(raw, "total"))
            row.reference_invoice_bexio_id = _pick(raw, "reference_invoice_id")
            row.raw = raw
            row.synced_at = dt.datetime.now(dt.timezone.utc)
            count += 1
    except BexioApiError as exc:
        if exc.status_code == 404:
            # Endpunkt-Pfad noch nicht verifiziert (Konzept 9.1) oder Feature "Gutschriften"
            # im Bexio-Plan nicht aktiviert - Sync nicht abbrechen, einfach ohne
            # Gutschriften weiterlaufen (Buchungen bleiben im Ist unkorrigiert sichtbar).
            logger.warning(
                "Gutschriften-Endpunkt (%s) lieferte 404 - wird uebersprungen, siehe "
                "_DOCUMENT_TYPE_TO_BEXIO_PATH['credit_note'].",
                _DOCUMENT_TYPE_TO_BEXIO_PATH["credit_note"],
            )
            db.rollback()
            return count
        raise
    db.commit()
    return count


_DOCUMENT_TYPE_TO_BEXIO_PATH = {
    "quote": "kb_offer",
    "order": "kb_order",
    "invoice": "kb_invoice",
    "credit_note": "kb_credit_voucher",
}


def sync_line_items(client: BexioClient, db: Session, document_type: str, document_ids: list[int]) -> int:
    bexio_path = _DOCUMENT_TYPE_TO_BEXIO_PATH[document_type]
    count = 0
    for document_id in document_ids:
        try:
            positions = client.get_positions(bexio_path, document_id)
        except Exception:
            logger.exception(
                "Positionen fuer %s %s konnten nicht geladen werden", document_type, document_id
            )
            continue
        for pos in positions:
            existing = (
                db.query(LineItem)
                .filter_by(
                    document_type=document_type,
                    document_id=document_id,
                    position_bexio_id=pos.get("id"),
                )
                .one_or_none()
            )
            if existing is None:
                existing = LineItem(
                    document_type=document_type,
                    document_id=document_id,
                    position_bexio_id=pos.get("id"),
                )
                db.add(existing)
            existing.product_code = _extract_product_code(pos)
            existing.description = pos.get("text", "") or pos.get("description", "")
            existing.quantity = _to_decimal(_pick(pos, "quantity"))
            existing.unit = pos.get("unit_id") and str(pos.get("unit_id"))
            existing.unit_price = _to_decimal(_pick(pos, "unit_price"))
            existing.total = _to_decimal(_pick(pos, "position_total"))
            existing.raw = pos
            count += 1
    db.commit()
    return count


def full_sync(db: Session, settings: Settings | None = None) -> dict:
    """Kompletter Sync-Lauf: Rohdaten laden, Positionen laden, Bookings neu berechnen."""
    settings = settings or get_settings()
    client = BexioClient(db, settings)
    missing_contact_ids: set[int] = set()

    stats = {
        "contacts": sync_contacts(client, db),
        "quotes": sync_quotes(client, db, missing_contact_ids),
        "orders": sync_orders(client, db, missing_contact_ids),
        "invoices": sync_invoices(client, db, missing_contact_ids),
        "credit_notes": sync_credit_notes(client, db, missing_contact_ids),
    }
    stats["quote_positions"] = sync_line_items(
        client, db, "quote", [q.id for q in db.query(Quote.id)]
    )
    stats["order_positions"] = sync_line_items(
        client, db, "order", [o.id for o in db.query(Order.id)]
    )
    stats["invoice_positions"] = sync_line_items(
        client, db, "invoice", [i.id for i in db.query(Invoice.id)]
    )

    stats["bookings"] = rebuild_bookings(db, settings)
    return stats


# ----------------------------------------------------------------------
# Transformationspipeline: Verkettung + Abgrenzung + Soll/Ist (Konzept Abschnitt 6)
# ----------------------------------------------------------------------
def _credit_voucher_net(invoice: Invoice) -> Decimal | None:
    """Verrechnete Gutschriften einer Rechnung, umgerechnet auf netto.

    Bexio's kb_credit_voucher-Endpunkt liefert 404 (siehe sync_credit_notes), aber
    jede Rechnung traegt "total_credit_vouchers" (brutto, wie total_gross). Fuer den
    netto ausgewiesenen Umsatz wird der Betrag im Verhaeltnis net/gross umgerechnet.
    None, wenn keine Gutschrift verrechnet wurde."""
    raw = invoice.raw or {}
    credit_gross = _to_decimal(raw.get("total_credit_vouchers"))
    if credit_gross <= 0:
        return None
    gross = _to_decimal(raw.get("total_gross"))
    net = _to_decimal(raw.get("total_net"))
    if gross > 0 and net > 0:
        return (credit_gross * net / gross).quantize(Decimal("0.01"))
    return credit_gross


def _document_metrics(
    db: Session,
    document_type: str,
    document_id: int,
    total: Decimal,
    physical_nights: int | None = None,
) -> DocumentMetrics:
    """Aggregiert Positionsmengen eines Dokuments zu Naechten/PAX.

    Bexio liefert kein eigenes PAX-/Teilnehmerzahl-Feld (Konzept 9.2). Verifiziert
    gegen einen echten Beleg: die Basis-Uebernachtungsposition (LH-UEB) wird "pro
    Person und Nacht" verrechnet - die PAX-Zahl laesst sich also herleiten als
    Menge / tatsaechliche Naechte (aus dem Leistungszeitraum), sofern kein
    explizites PAX-Feld gefunden wird.
    """
    items = db.query(LineItem).filter_by(document_type=document_type, document_id=document_id).all()
    nights_total = sum(
        (item.quantity for item in items if ist_uebernachtung(item.product_code)), Decimal("0")
    )
    pax = None
    for item in items:
        candidate = _pick(item.raw or {}, "pax")
        if candidate:
            pax = int(candidate)
            break
    if pax is None and physical_nights and nights_total > 0:
        derived = (nights_total / Decimal(physical_nights)).to_integral_value(rounding=ROUND_HALF_UP)
        pax = int(derived)
    return DocumentMetrics(pax=pax, nights=nights_total if nights_total > 0 else None, revenue=total)


def rebuild_bookings(db: Session, settings: Settings | None = None) -> int:
    """Baut alle Booking- und MonthlyAllocation-Zeilen aus dem aktuellen Rohdaten-Cache neu auf."""
    settings = settings or get_settings()
    effective = get_effective_config(db, settings)

    quotes = db.query(Quote).all()
    orders = db.query(Order).all()
    invoices = db.query(Invoice).all()
    credit_notes = db.query(CreditNote).all()

    chains: list[BookingChain] = build_booking_chains(
        quotes=[QuoteRef(q.id, q.document_nr, q.contact_id, q.title) for q in quotes],
        orders=[
            OrderRef(o.id, o.document_nr, o.contact_id, o.title, o.source_quote_bexio_id)
            for o in orders
        ],
        invoices=[
            InvoiceRef(i.id, i.document_nr, i.contact_id, i.title, i.source_order_bexio_id)
            for i in invoices
        ],
        credit_notes=[
            CreditNoteRef(c.id, c.document_nr, c.contact_id, c.title, c.reference_invoice_bexio_id)
            for c in credit_notes
        ],
    )

    quotes_by_id = {q.id: q for q in quotes}
    orders_by_id = {o.id: o for o in orders}
    invoices_by_id = {i.id: i for i in invoices}
    credit_notes_by_id = {c.id: c for c in credit_notes}
    contacts_by_id = {c.id: c for c in db.query(Contact).all()}

    seen_keys = set()
    booking_count = 0

    for chain in chains:
        seen_keys.add(chain.booking_key)
        quote_row = quotes_by_id.get(chain.quote.id) if chain.quote else None
        order_row = orders_by_id.get(chain.order.id) if chain.order else None
        invoice_row = invoices_by_id.get(chain.invoice.id) if chain.invoice else None
        credit_note_row = credit_notes_by_id.get(chain.credit_note.id) if chain.credit_note else None

        anchor_doc = order_row or invoice_row or quote_row
        title = anchor_doc.title if anchor_doc else ""
        doc_date = (order_row.order_date if order_row else None) or (
            invoice_row.invoice_date if invoice_row else None
        ) or (quote_row.quote_date if quote_row else None)
        service_start, service_end = extract_service_period(title, doc_date)
        physical_nights = (
            nights_between(service_start, service_end) if service_start and service_end else None
        )

        quote_metrics = (
            _document_metrics(db, "quote", quote_row.id, quote_row.total, physical_nights)
            if quote_row
            else None
        )
        order_metrics = (
            _document_metrics(db, "order", order_row.id, order_row.total, physical_nights)
            if order_row
            else None
        )
        invoice_metrics = (
            _document_metrics(db, "invoice", invoice_row.id, invoice_row.total, physical_nights)
            if invoice_row
            else None
        )

        credit_note_total = credit_note_row.total if credit_note_row else None
        if credit_note_total is None and invoice_row is not None:
            # Bexio liefert kb_credit_voucher nicht per API (404), aber jede Rechnung
            # traegt "total_credit_vouchers" - darueber koennen verrechnete
            # Gutschriften trotzdem vom Ist-Umsatz abgezogen werden (verifiziert
            # gegen das Buchhaltungsjournal 2025: 11 Gutschrift-Buchungen).
            credit_note_total = _credit_voucher_net(invoice_row)

        booking_metrics = compute_booking_metrics(
            quote=quote_metrics,
            order=order_metrics,
            invoice=invoice_metrics,
            credit_note_total=credit_note_total,
        )

        booking = db.query(Booking).filter_by(booking_key=chain.booking_key).one_or_none()
        if booking is None:
            booking = Booking(booking_key=chain.booking_key)
            db.add(booking)

        booking.quote_id = quote_row.id if quote_row else None
        booking.order_id = order_row.id if order_row else None
        booking.invoice_id = invoice_row.id if invoice_row else None
        booking.credit_note_id = credit_note_row.id if credit_note_row else None
        booking.contact_id = anchor_doc.contact_id if anchor_doc else None
        contact = contacts_by_id.get(booking.contact_id) if booking.contact_id else None
        booking.kunde = contact.name if contact else ""
        booking.anlass = title
        booking.linked_by = chain.linked_by
        booking.status = booking_metrics.status
        booking.service_start = service_start
        booking.service_end = service_end
        booking.invoice_date = invoice_row.invoice_date if invoice_row else None
        booking.payment_date = invoice_row.payment_date if invoice_row else None
        booking.pax_soll = booking_metrics.pax_soll
        booking.pax_ist = booking_metrics.pax_ist
        booking.nights_soll = booking_metrics.nights_soll
        booking.nights_ist = booking_metrics.nights_ist
        booking.umsatz_soll = booking_metrics.umsatz_soll
        booking.umsatz_ist = booking_metrics.umsatz_ist
        booking.updated_at = dt.datetime.now(dt.timezone.utc)
        db.flush()

        db.query(MonthlyAllocation).filter_by(booking_id=booking.id).delete()
        if service_start and service_end:
            soll_shares = allocate(
                AllocationInput(
                    service_start=service_start,
                    service_end=service_end,
                    revenue=booking_metrics.umsatz_soll,
                    pax=booking_metrics.pax_soll,
                    pax_nights=booking_metrics.nights_soll,
                ),
                mode=effective.allocation_mode,
            )
            ist_shares = allocate(
                AllocationInput(
                    service_start=service_start,
                    service_end=service_end,
                    revenue=booking_metrics.umsatz_ist,
                    pax=booking_metrics.pax_ist,
                    pax_nights=booking_metrics.nights_ist,
                ),
                mode=effective.allocation_mode,
            )
            ist_by_month = {(s.year, s.month): s for s in ist_shares}
            for soll in soll_shares:
                ist = ist_by_month.pop((soll.year, soll.month), None)
                db.add(
                    MonthlyAllocation(
                        booking_id=booking.id,
                        year=soll.year,
                        month=soll.month,
                        nights_soll=soll.nights,
                        nights_ist=ist.nights if ist else Decimal("0"),
                        days=soll.days,
                        pax_nights_soll=soll.pax_nights,
                        pax_nights_ist=ist.pax_nights if ist else Decimal("0"),
                        umsatz_soll=soll.revenue,
                        umsatz_ist=ist.revenue if ist else Decimal("0"),
                    )
                )
            for _, ist in ist_by_month.items():
                db.add(
                    MonthlyAllocation(
                        booking_id=booking.id,
                        year=ist.year,
                        month=ist.month,
                        nights_soll=Decimal("0"),
                        nights_ist=ist.nights,
                        days=ist.days,
                        pax_nights_soll=Decimal("0"),
                        pax_nights_ist=ist.pax_nights,
                        umsatz_soll=Decimal("0"),
                        umsatz_ist=ist.revenue,
                    )
                )
        booking_count += 1

    # Bookings entfernen, die es im aktuellen Sync nicht mehr gibt (z.B. geloeschtes Dokument).
    stale = db.query(Booking).filter(~Booking.booking_key.in_(seen_keys)).all() if seen_keys else []
    for row in stale:
        db.delete(row)

    db.commit()
    return booking_count
