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
