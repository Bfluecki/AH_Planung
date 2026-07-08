from app.matching.chain import (
    CreditNoteRef,
    InvoiceRef,
    LINKED_BY_HEURISTIC,
    LINKED_BY_NONE,
    LINKED_BY_REFERENCE,
    OrderRef,
    QuoteRef,
    build_booking_chains,
)


def test_reference_based_chain_full():
    quotes = [QuoteRef(id=1, document_nr="AN-00046", contact_id=100, title="Kurswoche Mai 2026")]
    orders = [
        OrderRef(id=10, document_nr="AU-00061", contact_id=100, title="Kurswoche Mai 2026",
                 source_quote_bexio_id=1)
    ]
    invoices = [
        InvoiceRef(id=20, document_nr="RE-00272", contact_id=100, title="Kurswoche Mai 2026",
                   source_order_bexio_id=10)
    ]
    credit_notes = [
        CreditNoteRef(id=30, document_nr="GS-00005", contact_id=100, title="Storno",
                      reference_invoice_bexio_id=20)
    ]

    chains = build_booking_chains(quotes, orders, invoices, credit_notes)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.booking_key == "AU-00061"
    assert chain.quote.id == 1
    assert chain.invoice.id == 20
    assert chain.credit_note.id == 30
    assert chain.linked_by == LINKED_BY_REFERENCE


def test_heuristic_fallback_when_no_reference():
    quotes = []
    orders = [OrderRef(id=10, document_nr="AU-00068", contact_id=200, title="Vereinsanlass Sommer 2026")]
    invoices = [
        InvoiceRef(id=21, document_nr="RE-00137", contact_id=200, title="Vereinsanlass Sommer 2026")
    ]
    chains = build_booking_chains(quotes, orders, invoices, [])

    assert len(chains) == 1
    assert chains[0].invoice.id == 21
    assert chains[0].linked_by == LINKED_BY_HEURISTIC


def test_no_match_for_different_contact():
    orders = [OrderRef(id=10, document_nr="AU-00068", contact_id=200, title="Vereinsanlass Sommer 2026")]
    invoices = [
        InvoiceRef(id=21, document_nr="RE-00137", contact_id=999, title="Vereinsanlass Sommer 2026")
    ]
    chains = build_booking_chains([], orders, invoices, [])

    assert chains[0].invoice is None
    assert chains[0].linked_by == LINKED_BY_NONE


def test_quote_without_order_stays_as_pipeline_booking():
    quotes = [QuoteRef(id=5, document_nr="AN-00099", contact_id=300, title="Anfrage Herbstlager")]
    chains = build_booking_chains(quotes, [], [], [])

    assert len(chains) == 1
    assert chains[0].booking_key == "AN-00099"
    assert chains[0].order is None


def test_order_is_primary_key_even_with_multiple_bookings_same_contact():
    orders = [
        OrderRef(id=10, document_nr="AU-00001", contact_id=100, title="Kurswoche Mai"),
        OrderRef(id=11, document_nr="AU-00002", contact_id=100, title="Vereinsanlass Herbst"),
    ]
    chains = build_booking_chains([], orders, [], [])
    keys = {c.booking_key for c in chains}
    assert keys == {"AU-00001", "AU-00002"}


def test_direct_invoice_without_order_becomes_own_booking():
    # Direktrechnung ohne Offerte/Auftrag (real: Miete, Kiosk, Kleinanlaesse) -
    # muss als eigenstaendige Buchung erscheinen, sonst fehlt ihr Umsatz komplett.
    invoices = [
        InvoiceRef(id=21, document_nr="RE-00137", contact_id=200, title="Miete September 2026")
    ]
    chains = build_booking_chains([], [], invoices, [])

    assert len(chains) == 1
    assert chains[0].booking_key == "RE-00137"
    assert chains[0].invoice.id == 21
    assert chains[0].order is None
    assert chains[0].linked_by == LINKED_BY_NONE


def test_direct_invoice_keeps_its_credit_note():
    invoices = [
        InvoiceRef(id=21, document_nr="RE-00137", contact_id=200, title="Anlass April 2026")
    ]
    credit_notes = [
        CreditNoteRef(id=30, document_nr="GS-00005", contact_id=200, title="Storno",
                      reference_invoice_bexio_id=21)
    ]
    chains = build_booking_chains([], [], invoices, credit_notes)

    assert chains[0].credit_note.id == 30


def test_invoice_matched_to_order_not_duplicated_as_direct_booking():
    orders = [OrderRef(id=10, document_nr="AU-00068", contact_id=200, title="Vereinsanlass Sommer 2026")]
    invoices = [
        InvoiceRef(id=21, document_nr="RE-00137", contact_id=200, title="Vereinsanlass Sommer 2026")
    ]
    chains = build_booking_chains([], orders, invoices, [])

    assert len(chains) == 1  # nur die Auftrags-Buchung, keine zweite Direkt-Buchung
    assert chains[0].booking_key == "AU-00068"
    assert chains[0].invoice.id == 21
