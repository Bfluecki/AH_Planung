import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from decimal import Decimal

from app.bexio.client import BexioApiError
from app.db import Base
from app.models import LineItem
from app.sync.service import _document_metrics, _extract_product_code, _pick, sync_credit_notes


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class _RaisingClient:
    """Simuliert einen Bexio-Endpunkt, der mit einem HTTP-Statuscode fehlschlaegt."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def list_credit_notes(self):
        raise BexioApiError(self.status_code, "Not Found")
        yield  # pragma: no cover - macht die Methode zu einem Generator


def test_credit_note_404_is_skipped_without_crashing_sync(db_session):
    client = _RaisingClient(404)
    count = sync_credit_notes(client, db_session, missing_contact_ids=set())
    assert count == 0


def test_credit_note_other_error_still_propagates(db_session):
    client = _RaisingClient(500)
    with pytest.raises(BexioApiError):
        sync_credit_notes(client, db_session, missing_contact_ids=set())


def test_extract_product_code_from_real_bexio_position_text():
    # Bexio liefert bei kb_position_article keinen eigenen Produktcode-Feld - der
    # Code steht als Freitext im HTML-Beschreibungsfeld "text" (echtes Beispiel aus
    # einem Live-Sync).
    raw = {
        "id": 2965,
        "text": (
            "<strong>Fr&uuml;hst&uuml;ck</strong><br />Produktcode: AH-FRU<br />\r\n"
            "<ul>\r\n<li>Fr&uuml;hst&uuml;ck pro Person</li>\r\n</ul>"
        ),
        "position_total": "18.000000",
    }
    assert _extract_product_code(raw) == "AH-FRU"


def test_extract_product_code_with_hyphenated_code():
    raw = {"text": "<strong>Uebernachtung - Doppelzimmerzuschlag</strong><br />Produktcode: LH-UEB-DZ<br />"}
    assert _extract_product_code(raw) == "LH-UEB-DZ"


def test_extract_product_code_falls_back_when_no_text_match():
    assert _extract_product_code({"text": "Keine Produktcode-Angabe hier"}) is None
    assert _extract_product_code({}) is None


def test_extract_product_code_decodes_html_entities():
    # Echter Fund: "AH-K&uuml;che" statt "AH-Küche" landete unentschluesselt als
    # eigener "unbekannter" Produktcode im Report.
    raw = {"text": "Produktcode: AH-K&uuml;che<br />"}
    assert _extract_product_code(raw) == "AH-Küche"


def test_extract_product_code_ignores_nbsp_only_match():
    # "Produktcode: &nbsp;" darf nicht als eigener Pseudo-Code durchgehen.
    assert _extract_product_code({"text": "Produktcode: &nbsp;<br />"}) is None


def test_extract_product_code_with_dots_and_spaces():
    # Getraenke-Codes aus dem echten Produktkatalog enthalten Punkte/Leerzeichen,
    # die im urspruenglichen [A-Za-z0-9-]-Muster abgeschnitten wurden.
    raw = {"text": "<strong>Pinot Noir</strong><br />Produktcode: 0.5 PN<br />"}
    assert _extract_product_code(raw) == "0.5 PN"


def test_extract_product_code_with_plus_sign():
    raw = {"text": "Produktcode: AH-SEM-PAU+<br />"}
    assert _extract_product_code(raw) == "AH-SEM-PAU+"


def test_document_total_prefers_net_over_gross():
    # Echte kb_invoice-Antwort: total_gross/total sind identisch (inkl. MwSt),
    # total_net ist der Betrag ohne MwSt - Umsatz soll netto ausgewiesen werden.
    raw = {
        "total_gross": "2999.000000",
        "total_net": "2823.900000",
        "total_taxes": "175.1010",
        "total": "2999.000000",
    }
    assert _pick(raw, "total") == "2823.900000"


def test_document_total_falls_back_to_gross_when_net_missing():
    raw = {"total_gross": "1000.00", "total": "1000.00"}
    assert _pick(raw, "total") == "1000.00"


def test_pax_is_derived_from_base_night_quantity_when_no_pax_field(db_session):
    # Echter Beleg: LH-UEB wird "pro Person und Nacht" verrechnet, hier 16.00 fuer
    # einen Aufenthalt von 4 Naechten -> 4 Personen.
    db_session.add(
        LineItem(
            document_type="order",
            document_id=1,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={"text": "Produktcode: LH-UEB"},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 1, total=Decimal("1000"), physical_nights=4)
    assert metrics.pax == 4
    assert metrics.nights == Decimal("16.00")


def test_pax_not_derived_without_known_physical_nights(db_session):
    db_session.add(
        LineItem(
            document_type="order",
            document_id=2,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 2, total=Decimal("1000"), physical_nights=None)
    assert metrics.pax is None


def test_explicit_pax_field_takes_precedence_over_derivation(db_session):
    db_session.add(
        LineItem(
            document_type="order",
            document_id=3,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={"pax": 7},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 3, total=Decimal("1000"), physical_nights=4)
    assert metrics.pax == 7  # nicht 4 - explizites Feld gewinnt gegenueber Herleitung


def test_credit_voucher_net_converts_gross_to_net():
    from app.models import Invoice
    from app.sync.service import _credit_voucher_net

    # Rechnung 1081 brutto / 1000 netto, davon 108.10 brutto per Gutschrift verrechnet
    # -> netto-Anteil 100.00.
    inv = Invoice(
        id=1, document_nr="RE-001", total=Decimal("1000.00"),
        raw={"total_gross": "1081.00", "total_net": "1000.00", "total_credit_vouchers": "108.10"},
    )
    assert _credit_voucher_net(inv) == Decimal("100.00")


def test_credit_voucher_net_returns_none_without_vouchers():
    from app.models import Invoice
    from app.sync.service import _credit_voucher_net

    inv = Invoice(id=2, document_nr="RE-002", total=Decimal("500.00"),
                   raw={"total_gross": "540.50", "total_net": "500.00", "total_credit_vouchers": "0.000000"})
    assert _credit_voucher_net(inv) is None
    assert _credit_voucher_net(Invoice(id=3, document_nr="RE-003", total=Decimal("1"), raw={})) is None


def test_rebuild_direct_cancelled_and_draft_invoices(db_session):
    import datetime as dt

    from app.models import Booking, Contact, Invoice
    from app.sync.service import rebuild_bookings

    db_session.add(Contact(id=1, contact_nr="K-1", name="Kunde A"))
    # Direktrechnung ohne Auftrag, bezahlt (Status 9) -> eigene Buchung "verrechnet"
    db_session.add(Invoice(id=1, document_nr="RE-100", contact_id=1,
                            title="Miete 05. September 2026", invoice_date=dt.date(2026, 9, 1),
                            total=Decimal("500.00"), raw={"kb_item_status_id": 9}))
    # Stornierte Rechnung (Status 19) -> Buchung "storniert", Ist = 0
    db_session.add(Invoice(id=2, document_nr="RE-101", contact_id=1,
                            title="Anlass 10. September 2026", invoice_date=dt.date(2026, 9, 2),
                            total=Decimal("300.00"), raw={"kb_item_status_id": 19}))
    # Entwurf (Status 7) ohne Auftrag -> gar keine Buchung
    db_session.add(Invoice(id=3, document_nr="RE-102", contact_id=1,
                            title="Anlass 20. September 2026", invoice_date=dt.date(2026, 9, 3),
                            total=Decimal("999.00"), raw={"kb_item_status_id": 7}))
    db_session.commit()

    count = rebuild_bookings(db_session)
    assert count == 2

    bookings = {b.booking_key: b for b in db_session.query(Booking).all()}
    assert set(bookings) == {"RE-100", "RE-101"}
    assert bookings["RE-100"].status == "verrechnet"
    assert bookings["RE-100"].umsatz_ist == Decimal("500.00")
    assert bookings["RE-101"].status == "storniert"
    assert bookings["RE-101"].umsatz_ist == Decimal("0.00")
