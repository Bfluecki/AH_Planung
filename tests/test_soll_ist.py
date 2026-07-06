from decimal import Decimal

from app.domain.soll_ist import (
    DocumentMetrics,
    STATUS_BEAUFTRAGT,
    STATUS_NUR_ANGEBOT,
    STATUS_STORNIERT,
    STATUS_VERRECHNET,
    compute_booking_metrics,
)


def test_nur_angebot_when_only_quote():
    quote = DocumentMetrics(pax=30, nights=Decimal("3"), revenue=Decimal("3000"))
    metrics = compute_booking_metrics(quote=quote, order=None, invoice=None)
    assert metrics.status == STATUS_NUR_ANGEBOT
    assert metrics.pax_soll == 30
    assert metrics.pax_ist is None
    assert metrics.umsatz_ist == Decimal("0")


def test_beauftragt_when_order_but_no_invoice():
    order = DocumentMetrics(pax=30, nights=Decimal("3"), revenue=Decimal("3000"))
    metrics = compute_booking_metrics(quote=None, order=order, invoice=None)
    assert metrics.status == STATUS_BEAUFTRAGT
    assert metrics.pax_soll == 30
    assert metrics.umsatz_soll == Decimal("3000")


def test_pax_diff_between_order_and_invoice():
    order = DocumentMetrics(pax=30, nights=Decimal("3"), revenue=Decimal("3000"))
    invoice = DocumentMetrics(pax=28, nights=Decimal("3"), revenue=Decimal("2800"))
    metrics = compute_booking_metrics(quote=None, order=order, invoice=invoice)
    assert metrics.status == STATUS_VERRECHNET
    assert metrics.pax_soll == 30
    assert metrics.pax_ist == 28
    assert metrics.pax_diff == -2
    assert metrics.umsatz_diff == Decimal("-200")


def test_credit_note_fully_cancels_invoice_marks_storniert():
    order = DocumentMetrics(pax=20, nights=Decimal("2"), revenue=Decimal("2000"))
    invoice = DocumentMetrics(pax=20, nights=Decimal("2"), revenue=Decimal("2000"))
    metrics = compute_booking_metrics(
        quote=None, order=order, invoice=invoice, credit_note_total=Decimal("2000")
    )
    assert metrics.status == STATUS_STORNIERT
    assert metrics.umsatz_ist == Decimal("0")
    # Soll/Pipeline-Werte bleiben trotz Storno sichtbar (Konzept Abschnitt 4)
    assert metrics.umsatz_soll == Decimal("2000")


def test_partial_credit_note_reduces_ist_but_stays_verrechnet():
    order = DocumentMetrics(pax=20, nights=Decimal("2"), revenue=Decimal("2000"))
    invoice = DocumentMetrics(pax=20, nights=Decimal("2"), revenue=Decimal("2000"))
    metrics = compute_booking_metrics(
        quote=None, order=order, invoice=invoice, credit_note_total=Decimal("500")
    )
    assert metrics.status == STATUS_VERRECHNET
    assert metrics.umsatz_ist == Decimal("1500")
