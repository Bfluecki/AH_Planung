"""Soll-/Ist-Abgleich je Buchung (Konzept Abschnitt 4).

Soll kommt aus dem Auftrag (Order), Ist aus der Rechnung (Invoice) abzueglich
etwaiger Gutschriften (Credit Note). Angebote (Quote) liefern nur dann Soll-Werte,
wenn noch kein Auftrag existiert (reine Pipeline).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

STATUS_NUR_ANGEBOT = "nur_angebot"
STATUS_BEAUFTRAGT = "beauftragt"
STATUS_VERRECHNET = "verrechnet"
STATUS_STORNIERT = "storniert"


@dataclass
class DocumentMetrics:
    pax: int | None = None
    nights: Decimal | None = None
    revenue: Decimal = Decimal("0")


@dataclass
class BookingMetrics:
    status: str
    pax_soll: int | None
    pax_ist: int | None
    nights_soll: Decimal
    nights_ist: Decimal
    umsatz_soll: Decimal
    umsatz_ist: Decimal

    @property
    def pax_diff(self) -> int | None:
        if self.pax_soll is None or self.pax_ist is None:
            return None
        return self.pax_ist - self.pax_soll

    @property
    def umsatz_diff(self) -> Decimal:
        return self.umsatz_ist - self.umsatz_soll


def compute_booking_metrics(
    quote: DocumentMetrics | None,
    order: DocumentMetrics | None,
    invoice: DocumentMetrics | None,
    credit_note_total: Decimal | None = None,
) -> BookingMetrics:
    """Errechnet Soll/Ist/Diff + Status fuer eine verkettete Buchung.

    Reihenfolge Soll-Quelle: Order > Quote (reine Pipeline ohne Auftrag).
    Ist-Quelle: Invoice, reduziert um credit_note_total (Gutschriften).
    """
    soll_source = order or quote
    pax_soll = soll_source.pax if soll_source else None
    nights_soll = soll_source.nights if soll_source and soll_source.nights is not None else Decimal("0")
    umsatz_soll = soll_source.revenue if soll_source else Decimal("0")

    pax_ist = invoice.pax if invoice else None
    nights_ist = invoice.nights if invoice and invoice.nights is not None else Decimal("0")
    umsatz_ist = invoice.revenue if invoice else Decimal("0")
    if credit_note_total:
        umsatz_ist -= credit_note_total

    if credit_note_total and invoice and umsatz_ist <= Decimal("0"):
        status = STATUS_STORNIERT
    elif invoice is not None:
        status = STATUS_VERRECHNET
    elif order is not None:
        status = STATUS_BEAUFTRAGT
    else:
        status = STATUS_NUR_ANGEBOT

    return BookingMetrics(
        status=status,
        pax_soll=pax_soll,
        pax_ist=pax_ist,
        nights_soll=nights_soll,
        nights_ist=nights_ist,
        umsatz_soll=umsatz_soll,
        umsatz_ist=umsatz_ist,
    )
