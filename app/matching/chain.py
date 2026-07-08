"""Verkettung Angebot -> Auftrag -> Rechnung -> Gutschrift (Konzept Abschnitt 2).

Reine Domain-Logik ohne DB-Abhaengigkeit (leichter zu testen); app/sync/service.py
uebersetzt ORM-Zeilen in die *Ref-Dataclasses hier und schreibt das Ergebnis
(Booking-Zeilen) zurueck in die DB.

Verkettungsstrategie:
1. Harte Referenz zuerst: Order.source_quote_bexio_id -> Quote.id,
   Invoice.source_order_bexio_id -> Order.id, CreditNote.reference_invoice_bexio_id -> Invoice.id.
   Diese Felder muessen in Phase 1 gegen die reale Bexio-API verifiziert werden
   (siehe Konzept Abschnitt 2, "Umsetzungsauftrag an Claude Code") - Feldnamen sind
   Platzhalter, bis die tatsaechliche API-Antwort vorliegt.
2. Fallback-Heuristik, falls keine harte Referenz vorhanden ist: gleiche Kundennummer
   (contact_id) + Titelaehnlichkeit (Wort-Overlap). Ergebnis wird als linked_by="heuristic"
   markiert, damit es im Audit-Trail nachvollziehbar bleibt (Konzept Abschnitt 2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LINKED_BY_REFERENCE = "reference"
LINKED_BY_HEURISTIC = "heuristic"
LINKED_BY_NONE = "none"

_STOPWORDS = {
    "der", "die", "das", "und", "von", "vom", "bis", "zum", "zur", "im", "in", "au",
    "du", "et", "la", "le", "les", "de", "des", "pour", "mit", "fuer", "für", "a",
}
_WORD_RE = re.compile(r"[a-zà-öø-ÿ0-9]+", re.IGNORECASE)

HEURISTIC_TITLE_OVERLAP_THRESHOLD = 0.5


@dataclass
class QuoteRef:
    id: int
    document_nr: str
    contact_id: int | None
    title: str


@dataclass
class OrderRef:
    id: int
    document_nr: str
    contact_id: int | None
    title: str
    source_quote_bexio_id: int | None = None


@dataclass
class InvoiceRef:
    id: int
    document_nr: str
    contact_id: int | None
    title: str
    source_order_bexio_id: int | None = None


@dataclass
class CreditNoteRef:
    id: int
    document_nr: str
    contact_id: int | None
    title: str
    reference_invoice_bexio_id: int | None = None


@dataclass
class BookingChain:
    booking_key: str
    quote: QuoteRef | None
    order: OrderRef | None
    invoice: InvoiceRef | None
    credit_note: CreditNoteRef | None
    linked_by: str


def _title_words(title: str) -> set[str]:
    words = {w.lower() for w in _WORD_RE.findall(title or "")}
    return words - _STOPWORDS


def _title_similarity(a: str, b: str) -> float:
    wa, wb = _title_words(a), _title_words(b)
    if not wa or not wb:
        return 0.0
    intersection = wa & wb
    union = wa | wb
    return len(intersection) / len(union)


def _heuristic_match(contact_id: int | None, title: str, candidates: list) -> object | None:
    best = None
    best_score = 0.0
    for cand in candidates:
        if cand.contact_id != contact_id:
            continue
        score = _title_similarity(title, cand.title)
        if score > best_score:
            best_score = score
            best = cand
    if best is not None and best_score >= HEURISTIC_TITLE_OVERLAP_THRESHOLD:
        return best
    return None


def build_booking_chains(
    quotes: list[QuoteRef],
    orders: list[OrderRef],
    invoices: list[InvoiceRef],
    credit_notes: list[CreditNoteRef],
) -> list[BookingChain]:
    """Baut alle Buchungsketten. Order ist der primaere Anker (Konzept Abschnitt 2)."""
    quotes_by_id = {q.id: q for q in quotes}
    invoices_by_order_ref: dict[int, list[InvoiceRef]] = {}
    for inv in invoices:
        if inv.source_order_bexio_id is not None:
            invoices_by_order_ref.setdefault(inv.source_order_bexio_id, []).append(inv)

    credit_notes_by_invoice_ref: dict[int, list[CreditNoteRef]] = {}
    for cn in credit_notes:
        if cn.reference_invoice_bexio_id is not None:
            credit_notes_by_invoice_ref.setdefault(cn.reference_invoice_bexio_id, []).append(cn)

    matched_invoice_ids: set[int] = set()
    matched_quote_ids: set[int] = set()
    matched_credit_note_ids: set[int] = set()
    chains: list[BookingChain] = []

    for order in orders:
        linked_by = LINKED_BY_NONE

        # 1) Quote-Verkettung: harte Referenz, sonst Heuristik.
        quote = None
        if order.source_quote_bexio_id is not None:
            quote = quotes_by_id.get(order.source_quote_bexio_id)
            if quote is not None:
                linked_by = LINKED_BY_REFERENCE
        if quote is None:
            candidates = [q for q in quotes if q.id not in matched_quote_ids]
            quote = _heuristic_match(order.contact_id, order.title, candidates)
            if quote is not None:
                linked_by = LINKED_BY_HEURISTIC
        if quote is not None:
            matched_quote_ids.add(quote.id)

        # 2) Invoice-Verkettung: harte Referenz zuerst.
        invoice = None
        candidate_invoices = invoices_by_order_ref.get(order.id, [])
        if candidate_invoices:
            invoice = candidate_invoices[0]
            matched_invoice_ids.add(invoice.id)
            if linked_by == LINKED_BY_NONE:
                linked_by = LINKED_BY_REFERENCE
        else:
            unmatched = [i for i in invoices if i.id not in matched_invoice_ids]
            invoice = _heuristic_match(order.contact_id, order.title, unmatched)
            if invoice is not None:
                matched_invoice_ids.add(invoice.id)
                if linked_by == LINKED_BY_NONE:
                    linked_by = LINKED_BY_HEURISTIC

        # 3) Credit-Note-Verkettung ueber die gefundene Rechnung.
        credit_note = None
        if invoice is not None:
            cn_candidates = credit_notes_by_invoice_ref.get(invoice.id, [])
            if cn_candidates:
                credit_note = cn_candidates[0]
                matched_credit_note_ids.add(credit_note.id)

        chains.append(
            BookingChain(
                booking_key=order.document_nr,
                quote=quote,
                order=order,
                invoice=invoice,
                credit_note=credit_note,
                linked_by=linked_by,
            )
        )

    # Reine Pipeline: Angebote ohne Auftrag laufen unter ihrer eigenen Nummer.
    for quote in quotes:
        if quote.id in matched_quote_ids:
            continue
        chains.append(
            BookingChain(
                booking_key=quote.document_nr,
                quote=quote,
                order=None,
                invoice=None,
                credit_note=None,
                linked_by=LINKED_BY_NONE,
            )
        )

    # Direktrechnungen: Rechnungen ohne (gefundenen) Auftrag werden eigenstaendige
    # Buchungen unter ihrer Rechnungsnummer - sonst fehlt deren Umsatz komplett in
    # der Planung (real: Miete, Kiosk, Kleinanlaesse ohne vorgaengige Offerte;
    # beim ersten Live-Abgleich waren so ~44% des Rechnungsumsatzes unsichtbar).
    for invoice in invoices:
        if invoice.id in matched_invoice_ids:
            continue
        credit_note = None
        cn_candidates = credit_notes_by_invoice_ref.get(invoice.id, [])
        if cn_candidates:
            credit_note = cn_candidates[0]
            matched_credit_note_ids.add(credit_note.id)
        chains.append(
            BookingChain(
                booking_key=invoice.document_nr,
                quote=None,
                order=None,
                invoice=invoice,
                credit_note=credit_note,
                linked_by=LINKED_BY_NONE,
            )
        )

    return chains
